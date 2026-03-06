package api

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/gorilla/websocket"
)

// Client talks to the claude-manager backend API.
type Client struct {
	BaseURL    string
	HTTPClient *http.Client
}

// NewClient creates a new API client targeting the given base URL.
func NewClient(baseURL string) *Client {
	baseURL = strings.TrimRight(baseURL, "/")
	return &Client{
		BaseURL: baseURL,
		HTTPClient: &http.Client{
			Timeout: 10 * time.Second,
		},
	}
}

// ── helpers ──────────────────────────────────────────────────────────────────

func (c *Client) get(path string, out interface{}) error {
	resp, err := c.HTTPClient.Get(c.BaseURL + path)
	if err != nil {
		return fmt.Errorf("request failed: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		body, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("HTTP %d: %s", resp.StatusCode, string(body))
	}
	return json.NewDecoder(resp.Body).Decode(out)
}

func (c *Client) post(path string, body interface{}, out interface{}) error {
	var r io.Reader
	if body != nil {
		b, err := json.Marshal(body)
		if err != nil {
			return err
		}
		r = bytes.NewReader(b)
	}
	resp, err := c.HTTPClient.Post(c.BaseURL+path, "application/json", r)
	if err != nil {
		return fmt.Errorf("request failed: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		b, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("HTTP %d: %s", resp.StatusCode, string(b))
	}
	if out != nil {
		return json.NewDecoder(resp.Body).Decode(out)
	}
	return nil
}

func (c *Client) delete(path string) error {
	req, err := http.NewRequest(http.MethodDelete, c.BaseURL+path, nil)
	if err != nil {
		return err
	}
	resp, err := c.HTTPClient.Do(req)
	if err != nil {
		return fmt.Errorf("request failed: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		b, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("HTTP %d: %s", resp.StatusCode, string(b))
	}
	return nil
}

// ── Projects ─────────────────────────────────────────────────────────────────

// GetProjects lists all managed projects.
func (c *Client) GetProjects() ([]Project, error) {
	var out []Project
	err := c.get("/api/projects", &out)
	return out, err
}

// GetProject fetches a single project by name.
func (c *Client) GetProject(name string) (*Project, error) {
	var out Project
	err := c.get("/api/projects/"+url.PathEscape(name), &out)
	return &out, err
}

// ── Agents ───────────────────────────────────────────────────────────────────

// GetAgents lists all active agents.
func (c *Client) GetAgents() ([]Agent, error) {
	var out []Agent
	err := c.get("/api/agents", &out)
	return out, err
}

// KillAgent stops an agent by session ID.
func (c *Client) KillAgent(sessionID string) error {
	return c.delete("/api/agents/" + url.PathEscape(sessionID))
}

// ── Dispatch ─────────────────────────────────────────────────────────────────

// Dispatch sends a task to a project.
func (c *Client) Dispatch(project, task, model string) (*DispatchResponse, error) {
	body := DispatchRequest{Task: task, Model: model}
	var out DispatchResponse
	err := c.post("/api/projects/"+url.PathEscape(project)+"/dispatch", body, &out)
	return &out, err
}

// ── Tasks ────────────────────────────────────────────────────────────────────

// GetTasks lists tasks for a project.
func (c *Client) GetTasks(project string) ([]Task, error) {
	var out []Task
	err := c.get("/api/projects/"+url.PathEscape(project)+"/tasks", &out)
	return out, err
}

// ── Health & Stats ───────────────────────────────────────────────────────────

// Health checks the backend health endpoint.
func (c *Client) Health() (*HealthResponse, error) {
	var out HealthResponse
	err := c.get("/api/health", &out)
	return &out, err
}

// Stats fetches global stats.
func (c *Client) Stats() (*StatsResponse, error) {
	var out StatsResponse
	err := c.get("/api/stats", &out)
	return &out, err
}

// ── Canvas ───────────────────────────────────────────────────────────────────

// GetWidgets lists canvas widgets for a project.
func (c *Client) GetWidgets(project string) ([]Widget, error) {
	var out []Widget
	err := c.get("/api/canvas/"+url.PathEscape(project), &out)
	return out, err
}

// DeleteWidget removes a widget.
func (c *Client) DeleteWidget(project, widgetID string) error {
	return c.delete("/api/canvas/" + url.PathEscape(project) + "/widgets/" + url.PathEscape(widgetID))
}

// CreateWidget creates a new widget.
func (c *Client) CreateWidget(project string, w map[string]interface{}) (*Widget, error) {
	var out Widget
	err := c.post("/api/canvas/"+url.PathEscape(project)+"/widgets", w, &out)
	return &out, err
}

// ── WebSocket ────────────────────────────────────────────────────────────────

// StreamWebSocket connects to the WS endpoint and calls handler for each event.
// It blocks until the connection is closed or handler returns an error.
func (c *Client) StreamWebSocket(handler func(event WSEvent) error) error {
	// Convert http:// to ws://
	wsURL := strings.Replace(c.BaseURL, "http://", "ws://", 1)
	wsURL = strings.Replace(wsURL, "https://", "wss://", 1)
	wsURL += "/ws"

	conn, _, err := websocket.DefaultDialer.Dial(wsURL, nil)
	if err != nil {
		return fmt.Errorf("websocket connect: %w", err)
	}
	defer conn.Close()

	// Send ping periodically to keep alive
	go func() {
		ticker := time.NewTicker(30 * time.Second)
		defer ticker.Stop()
		for range ticker.C {
			msg := map[string]string{"type": "ping"}
			if err := conn.WriteJSON(msg); err != nil {
				return
			}
		}
	}()

	for {
		_, message, err := conn.ReadMessage()
		if err != nil {
			if websocket.IsCloseError(err, websocket.CloseNormalClosure) {
				return nil
			}
			return fmt.Errorf("websocket read: %w", err)
		}
		var event WSEvent
		if err := json.Unmarshal(message, &event); err != nil {
			continue // skip malformed messages
		}
		if err := handler(event); err != nil {
			return err
		}
	}
}
