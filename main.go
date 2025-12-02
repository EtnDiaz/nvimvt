package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strings"

	"github.com/gdamore/tcell/v2"
	vault "github.com/hashicorp/vault/api"
	"github.com/rivo/tview"
	"gopkg.in/yaml.v3"
)

const (
	defaultMount      = "secret"
	defaultConfigPath = "/.config/nvimvt/config.yaml"
)

// Config holds persisted settings.
type Config struct {
	Address      string `yaml:"address"`
	MountPoint   string `yaml:"mount_point"`
	AuthMethod   string `yaml:"auth_method"`
	Token        string `yaml:"token"`
	Username     string `yaml:"username"`
	Password     string `yaml:"password"`
	K8sTokenPath string `yaml:"k8s_token_path"`
	K8sRole      string `yaml:"k8s_role"`
}

func defaultConfig() *Config {
	return &Config{
		MountPoint:   defaultMount,
		AuthMethod:   "token",
		K8sTokenPath: "/var/run/secrets/kubernetes.io/serviceaccount/token",
		K8sRole:      "default",
	}
}

func configPath() string {
	if custom := os.Getenv("NVIMVT_CONFIG"); custom != "" {
		return custom
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return defaultConfigPath
	}
	return filepath.Join(home, defaultConfigPath)
}

func loadConfig() *Config {
	cfg := defaultConfig()
	data, err := os.ReadFile(configPath())
	if err != nil {
		return cfg
	}
	if err := yaml.Unmarshal(data, cfg); err != nil {
		log.Printf("failed to parse config: %v", err)
	}
	return cfg
}

func saveConfig(cfg *Config) error {
	out, err := yaml.Marshal(cfg)
	if err != nil {
		return err
	}
	path := configPath()
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	return os.WriteFile(path, out, 0o600)
}

// App encapsulates TUI state.
type App struct {
	app    *tview.Application
	config *Config
	client *vault.Client

	tree      *tview.TreeView
	detail    *tview.TextView
	statusBar *tview.TextView
	help      *tview.TextView

	layout tview.Primitive
}

func newApp() *App {
	return &App{
		app:    tview.NewApplication(),
		config: loadConfig(),
	}
}

func (a *App) Run() error {
	a.tree = tview.NewTreeView().SetBorder(true).SetTitle(" KV Explorer ")
	a.tree.SetSelectedFunc(a.handleSelect)
	a.detail = tview.NewTextView().SetBorder(true).SetDynamicColors(true).SetTitle(" Secret ").SetWrap(true)
	a.statusBar = tview.NewTextView().SetDynamicColors(true)
	a.help = tview.NewTextView().SetBorder(true).SetTitle(" Help ")
	a.help.SetText("q quit • r reload • n new • e edit • d delete • v versions • b back • s save config • l login • ? toggle help")

	top := tview.NewFlex().AddItem(a.tree, 0, 1, true).AddItem(a.detail, 0, 2, false)
	root := tview.NewFlex().SetDirection(tview.FlexRow).
		AddItem(top, 0, 1, true).
		AddItem(a.help, 3, 0, false).
		AddItem(a.statusBar, 1, 0, false)

	a.layout = root
	a.app.SetRoot(root, true).EnableMouse(true)
	a.app.SetInputCapture(a.handleKeys)

	if err := a.ensureLogin(); err != nil {
		a.setStatus("[red]" + err.Error())
		a.promptLogin()
	} else {
		a.populateRoot()
	}

	return a.app.Run()
}

func (a *App) handleKeys(event *tcell.EventKey) *tcell.EventKey {
	switch event.Rune() {
	case 'q':
		a.app.Stop()
	case 'r':
		a.refreshSelection()
	case 'b':
		a.up()
	case 'n':
		a.promptNewSecret()
	case 'e':
		a.promptEditSecret()
	case 'd':
		a.confirmDelete()
	case 'v':
		a.showVersions()
	case 's':
		if err := saveConfig(a.config); err != nil {
			a.setStatus("[red]save failed: " + err.Error())
		} else {
			a.setStatus("config saved to " + configPath())
		}
	case 'l':
		a.promptLogin()
	case '?':
		a.toggleHelp()
	}

	switch event.Key() {
	case tcell.KeyBackspace, tcell.KeyBackspace2:
		a.up()
	case tcell.KeyEnter:
		a.handleSelect(a.tree.GetCurrentNode())
	}
	return event
}

func (a *App) setStatus(msg string) {
	a.statusBar.SetText(" " + msg)
}

func (a *App) ensureLogin() error {
	if a.config.Address == "" {
		return errors.New("Vault address not set. Press 'l' to login")
	}
	if err := a.login(); err != nil {
		return err
	}
	a.setStatus("connected to " + a.config.Address)
	return nil
}

func (a *App) login() error {
	cfg := vault.DefaultConfig()
	cfg.Address = a.config.Address
	client, err := vault.NewClient(cfg)
	if err != nil {
		return err
	}

	switch strings.ToLower(a.config.AuthMethod) {
	case "token":
		if a.config.Token == "" {
			return errors.New("token required")
		}
		client.SetToken(a.config.Token)
	case "userpass":
		if a.config.Username == "" || a.config.Password == "" {
			return errors.New("username/password required")
		}
		path := fmt.Sprintf("auth/userpass/login/%s", a.config.Username)
		resp, err := client.Logical().Write(path, map[string]interface{}{"password": a.config.Password})
		if err != nil {
			return err
		}
		if resp == nil || resp.Auth == nil {
			return errors.New("userpass login failed")
		}
		client.SetToken(resp.Auth.ClientToken)
	case "k8s", "k8s-secret", "kubernetes":
		tokenBytes, err := os.ReadFile(a.config.K8sTokenPath)
		if err != nil {
			return fmt.Errorf("read k8s token: %w", err)
		}
		payload := map[string]interface{}{
			"jwt":  strings.TrimSpace(string(tokenBytes)),
			"role": a.config.K8sRole,
		}
		resp, err := client.Logical().Write("auth/kubernetes/login", payload)
		if err != nil {
			return err
		}
		if resp == nil || resp.Auth == nil {
			return errors.New("kubernetes login failed")
		}
		client.SetToken(resp.Auth.ClientToken)
	default:
		return fmt.Errorf("unsupported auth method %s", a.config.AuthMethod)
	}

	a.client = client
	if a.config.MountPoint == "" {
		a.config.MountPoint = defaultMount
	}
	return nil
}

func (a *App) populateRoot() {
	root := tview.NewTreeNode(a.config.MountPoint + "/").SetReference("").SetSelectable(true)
	root.SetColor(tcell.ColorLightBlue)
	a.tree.SetRoot(root).SetCurrentNode(root)
	a.loadChildren(root)
}

func (a *App) loadChildren(node *tview.TreeNode) {
	ref := node.GetReference().(string)
	node.ClearChildren()
	list, err := a.list(ref)
	if err != nil {
		a.setStatus("[red]list failed: " + err.Error())
		return
	}
	for _, key := range list {
		childRef := strings.TrimPrefix(filepath.Join(ref, key), "/")
		child := tview.NewTreeNode(key).SetReference(childRef).SetSelectable(true)
		if strings.HasSuffix(key, "/") {
			child.SetColor(tcell.ColorLightBlue)
		}
		node.AddChild(child)
	}
}

func (a *App) list(path string) ([]string, error) {
	target := fmt.Sprintf("%s/metadata/%s", a.config.MountPoint, strings.TrimPrefix(path, "/"))
	target = strings.TrimSuffix(target, "/")
	resp, err := a.client.Logical().List(target)
	if err != nil {
		return nil, err
	}
	if resp == nil || resp.Data == nil {
		return []string{}, nil
	}
	rawKeys, ok := resp.Data["keys"].([]interface{})
	if !ok {
		return nil, errors.New("unexpected list response")
	}
	out := make([]string, 0, len(rawKeys))
	for _, k := range rawKeys {
		if s, ok := k.(string); ok {
			if strings.HasSuffix(s, "/") {
				out = append(out, s)
			} else {
				out = append(out, s)
			}
		}
	}
	return out, nil
}

func (a *App) handleSelect(node *tview.TreeNode) {
	ref := node.GetReference().(string)
	if strings.HasSuffix(node.GetText(), "/") {
		a.loadChildren(node)
		a.tree.SetCurrentNode(node)
		return
	}
	a.showSecret(ref)
}

func (a *App) up() {
	cur := a.tree.GetCurrentNode()
	if cur == nil {
		return
	}
	if parent := cur.GetParent(); parent != nil {
		a.tree.SetCurrentNode(parent)
	}
}

func (a *App) showSecret(path string) {
	secret, err := a.read(path)
	if err != nil {
		a.detail.SetText("[red]read failed: " + err.Error())
		a.setStatus("[red]read failed")
		return
	}
	data := secret.Data["data"]
	formatted, _ := json.MarshalIndent(data, "", "  ")
	meta, _ := secret.Data["metadata"].(map[string]interface{})
	version := meta["version"]
	created := meta["created_time"]
	updated := meta["updated_time"]
	info := fmt.Sprintf("[yellow]Path:[-] %s\n[yellow]Version:[-] %v\n[yellow]Created:[-] %v\n[yellow]Updated:[-] %v\n\n%s", path, version, created, updated, formatted)
	a.detail.SetText(info)
	a.setStatus(fmt.Sprintf("loaded %s (v%v)", path, version))
}

func (a *App) read(path string) (*vault.Secret, error) {
	target := fmt.Sprintf("%s/data/%s", a.config.MountPoint, strings.TrimPrefix(path, "/"))
	target = strings.TrimSuffix(target, "/")
	return a.client.Logical().Read(target)
}

func (a *App) refreshSelection() {
	node := a.tree.GetCurrentNode()
	if node == nil {
		return
	}
	ref := node.GetReference().(string)
	if strings.HasSuffix(node.GetText(), "/") || ref == "" {
		a.loadChildren(node)
		a.setStatus("refreshed")
	} else {
		a.showSecret(ref)
	}
}

func (a *App) promptLogin() {
	options := []string{"token", "userpass", "k8s"}
	form := tview.NewForm().
		AddInputField("Address", a.config.Address, 0, nil, func(v string) { a.config.Address = strings.TrimSpace(v) }).
		AddInputField("Mount", a.config.MountPoint, 0, nil, func(v string) { a.config.MountPoint = strings.TrimSpace(v) }).
		AddDropDown("Auth", options, indexOf(options, a.config.AuthMethod), func(option string, _ int) { a.config.AuthMethod = option }).
		AddInputField("Token", a.config.Token, 0, nil, func(v string) { a.config.Token = strings.TrimSpace(v) }).
		AddInputField("Username", a.config.Username, 0, nil, func(v string) { a.config.Username = strings.TrimSpace(v) }).
		AddPasswordField("Password", a.config.Password, 0, '*', func(v string) { a.config.Password = v }).
		AddInputField("K8s token", a.config.K8sTokenPath, 0, nil, func(v string) { a.config.K8sTokenPath = strings.TrimSpace(v) }).
		AddInputField("K8s role", a.config.K8sRole, 0, nil, func(v string) { a.config.K8sRole = strings.TrimSpace(v) })

	form.AddButton("Login", func() {
		if err := a.login(); err != nil {
			a.setStatus("[red]login failed: " + err.Error())
		} else {
			a.setStatus("connected")
			a.populateRoot()
		}
		a.popModal()
	})
	form.AddButton("Cancel", func() { a.popModal() })
	form.SetBorder(true).SetTitle(" Login ")

	a.pushModal(center(form, 80, 20), form)
}

func (a *App) promptNewSecret() {
	node := a.tree.GetCurrentNode()
	if node == nil || a.client == nil {
		return
	}
	base := node.GetReference().(string)
	if base != "" && !strings.HasSuffix(node.GetText(), "/") {
		base = filepath.Dir(base)
	}

	nameField := tview.NewInputField().SetLabel("Name: ")
	body := tview.NewTextArea().SetText("{\n  \"key\": \"value\"\n}")
	body.SetBorder(true).SetTitle(" JSON body ")

	save := tview.NewButton("Create").SetSelectedFunc(func() {
		name := strings.TrimSpace(nameField.GetText())
		content := body.GetText()
		if name == "" {
			a.setStatus("[red]name required")
			return
		}
		if err := a.write(filepath.Join(base, name), content); err != nil {
			a.setStatus("[red]create failed: " + err.Error())
		} else {
			a.setStatus("created " + name)
			a.refreshSelection()
		}
		a.popModal()
	})
	cancel := tview.NewButton("Cancel").SetSelectedFunc(func() { a.popModal() })

	layout := tview.NewFlex().SetDirection(tview.FlexRow).
		AddItem(nameField, 1, 0, true).
		AddItem(body, 0, 1, false).
		AddItem(tview.NewFlex().SetDirection(tview.FlexColumn).AddItem(save, 0, 1, false).AddItem(cancel, 0, 1, false), 1, 0, false)

	layout.SetBorder(true).SetTitle(" New secret in " + base)
	a.pushModal(center(layout, 90, 25), nameField)
}

func (a *App) promptEditSecret() {
	node := a.tree.GetCurrentNode()
	if node == nil || a.client == nil {
		return
	}
	ref := node.GetReference().(string)
	if ref == "" || strings.HasSuffix(node.GetText(), "/") {
		a.setStatus("[yellow]select a secret to edit")
		return
	}
	sec, err := a.read(ref)
	if err != nil {
		a.setStatus("[red]read failed: " + err.Error())
		return
	}
	current, _ := json.MarshalIndent(sec.Data["data"], "", "  ")

	body := tview.NewTextArea().SetText(string(current))
	body.SetBorder(true).SetTitle(" Edit JSON ")
	save := tview.NewButton("Save").SetSelectedFunc(func() {
		if err := a.write(ref, body.GetText()); err != nil {
			a.setStatus("[red]save failed: " + err.Error())
		} else {
			a.setStatus("updated " + ref)
			a.showSecret(ref)
		}
		a.popModal()
	})
	cancel := tview.NewButton("Cancel").SetSelectedFunc(func() { a.popModal() })

	layout := tview.NewFlex().SetDirection(tview.FlexRow).
		AddItem(body, 0, 1, true).
		AddItem(tview.NewFlex().SetDirection(tview.FlexColumn).AddItem(save, 0, 1, false).AddItem(cancel, 0, 1, false), 1, 0, false)
	layout.SetBorder(true).SetTitle(" Edit " + ref)
	a.pushModal(center(layout, 90, 25), body)
}

func (a *App) write(path, body string) error {
	var payload map[string]interface{}
	if err := json.Unmarshal([]byte(body), &payload); err != nil {
		return fmt.Errorf("invalid JSON: %w", err)
	}
	target := fmt.Sprintf("%s/data/%s", a.config.MountPoint, strings.TrimPrefix(path, "/"))
	target = strings.TrimSuffix(target, "/")
	_, err := a.client.Logical().Write(target, map[string]interface{}{"data": payload})
	return err
}

func (a *App) confirmDelete() {
	node := a.tree.GetCurrentNode()
	if node == nil || a.client == nil {
		return
	}
	ref := node.GetReference().(string)
	if ref == "" || strings.HasSuffix(node.GetText(), "/") {
		a.setStatus("[yellow]select a secret to delete")
		return
	}
	modal := tview.NewModal().
		SetText(fmt.Sprintf("Delete %s (all versions)?", ref)).
		AddButtons([]string{"Delete", "Cancel"}).
		SetDoneFunc(func(_ int, label string) {
			if label == "Delete" {
				if err := a.deleteSecret(ref); err != nil {
					a.setStatus("[red]delete failed: " + err.Error())
				} else {
					a.setStatus("deleted " + ref)
					if parent := node.GetParent(); parent != nil {
						a.tree.SetCurrentNode(parent)
						a.loadChildren(parent)
					}
				}
			}
			a.popModal()
		})
	a.pushModal(modal, modal)
}

func (a *App) deleteSecret(path string) error {
	target := fmt.Sprintf("%s/metadata/%s", a.config.MountPoint, strings.TrimPrefix(path, "/"))
	target = strings.TrimSuffix(target, "/")
	_, err := a.client.Logical().Delete(target)
	return err
}

func (a *App) showVersions() {
	node := a.tree.GetCurrentNode()
	if node == nil || a.client == nil {
		return
	}
	ref := node.GetReference().(string)
	if ref == "" || strings.HasSuffix(node.GetText(), "/") {
		a.setStatus("[yellow]select a secret")
		return
	}
	target := fmt.Sprintf("%s/metadata/%s", a.config.MountPoint, strings.TrimPrefix(ref, "/"))
	target = strings.TrimSuffix(target, "/")
	resp, err := a.client.Logical().Read(target)
	if err != nil {
		a.setStatus("[red]metadata read failed: " + err.Error())
		return
	}
	if resp == nil || resp.Data == nil {
		a.detail.SetText("no metadata")
		return
	}
	versions, _ := json.MarshalIndent(resp.Data["versions"], "", "  ")
	a.detail.SetText(fmt.Sprintf("[yellow]Versions for %s\n\n%s", ref, string(versions)))
}

func (a *App) toggleHelp() {
	if a.help.GetText(false) == "" {
		a.help.SetText("q quit • r reload • n new • e edit • d delete • v versions • b back • s save config • l login • ? toggle help")
	} else {
		a.help.SetText("")
	}
}

func (a *App) pushModal(p tview.Primitive, focus tview.Primitive) {
	a.app.SetRoot(p, true).SetFocus(focus)
}

func (a *App) popModal() {
	a.app.SetRoot(a.layout, true).SetFocus(a.tree)
}

func indexOf(options []string, v string) int {
	for i, opt := range options {
		if opt == v {
			return i
		}
	}
	return 0
}

func center(p tview.Primitive, width, height int) tview.Primitive {
	return tview.NewFlex().
		AddItem(nil, 0, 1, false).
		AddItem(tview.NewFlex().SetDirection(tview.FlexRow).
			AddItem(nil, 0, 1, false).
			AddItem(p, height, 0, true).
			AddItem(nil, 0, 1, false), width, 0, true).
		AddItem(nil, 0, 1, false)
}

func main() {
	if err := os.MkdirAll(filepath.Dir(configPath()), 0o700); err != nil {
		log.Fatalf("config dir: %v", err)
	}
	app := newApp()
	if err := app.Run(); err != nil {
		log.Fatalf("error: %v", err)
	}
}
