// Package api provides the HTTP/WebSocket client for the claude-manager backend.
package api

import (
	"fmt"
	"time"
)

// Project mirrors the ManagedProject Pydantic model.
type Project struct {
	Name             string   `json:"name"`
	Path             string   `json:"path"`
	Description      string   `json:"description,omitempty"`
	Goal             string   `json:"goal,omitempty"`
	Config           Config   `json:"config"`
	ActiveSessionIDs []string `json:"active_session_ids"`
}

// Config mirrors ProjectConfig.
type Config struct {
	Parallelism     int    `json:"parallelism"`
	Model           string `json:"model,omitempty"`
	MCPConfig       string `json:"mcp_config,omitempty"`
	DashboardPrompt string `json:"dashboard_prompt,omitempty"`
}

// Agent mirrors the session to_dict() output.
type Agent struct {
	SessionID           string   `json:"session_id"`
	ProjectName         string   `json:"project_name"`
	ProjectPath         string   `json:"project_path"`
	Task                string   `json:"task,omitempty"`
	Status              string   `json:"status"`
	Phase               string   `json:"phase"`
	Model               string   `json:"model,omitempty"`
	StartedAt           string   `json:"started_at,omitempty"`
	TurnCount           int      `json:"turn_count"`
	Milestones          []string `json:"milestones"`
	LastChunk           string   `json:"last_chunk,omitempty"`
	IsController        bool     `json:"is_controller"`
	TaskIndex           *int     `json:"task_index,omitempty"`
	HasPendingInjection bool     `json:"has_pending_injection"`
	PID                 *int     `json:"pid,omitempty"`
}

// ElapsedString returns a human-friendly duration since the agent started.
func (a Agent) ElapsedString() string {
	if a.StartedAt == "" {
		return "-"
	}
	// Try common formats
	for _, layout := range []string{
		time.RFC3339,
		time.RFC3339Nano,
		"2006-01-02T15:04:05",
		"2006-01-02 15:04:05",
	} {
		t, err := time.Parse(layout, a.StartedAt)
		if err == nil {
			d := time.Since(t)
			if d < time.Minute {
				return d.Round(time.Second).String()
			}
			if d < time.Hour {
				return d.Round(time.Second).String()
			}
			hours := int(d.Hours())
			mins := int(d.Minutes()) % 60
			if hours > 0 {
				return fmt.Sprintf("%dh%dm", hours, mins)
			}
			return fmt.Sprintf("%dm", mins)
		}
	}
	return a.StartedAt
}

// Task represents a project task.
type Task struct {
	Text   string `json:"text"`
	Status string `json:"status"`
	Index  int    `json:"index,omitempty"`
}

// HealthResponse from /api/health.
type HealthResponse struct {
	Status        string  `json:"status"`
	Uptime        float64 `json:"uptime"`
	Agents        int     `json:"agents"`
	WSConnections int     `json:"ws_connections"`
}

// StatsResponse from /api/stats.
type StatsResponse struct {
	TotalProjects int     `json:"total_projects"`
	TotalAgents   int     `json:"total_agents"`
	WorkingAgents int     `json:"working_agents"`
	IdleAgents    int     `json:"idle_agents"`
	UptimeSeconds float64 `json:"uptime_seconds"`
}

// DispatchRequest is the body for POST /api/projects/{name}/dispatch.
type DispatchRequest struct {
	Task  string `json:"task"`
	Model string `json:"model,omitempty"`
}

// DispatchResponse is the response from dispatch.
type DispatchResponse struct {
	SessionID string `json:"session_id,omitempty"`
	Status    string `json:"status,omitempty"`
	AgentIDs  []string `json:"agent_ids,omitempty"`
}

// Widget mirrors WidgetState.
type Widget struct {
	ID         string `json:"id"`
	Project    string `json:"project"`
	Title      string `json:"title"`
	Tab        string `json:"tab"`
	TemplateID string `json:"template_id,omitempty"`
	GSX        *int   `json:"gs_x,omitempty"`
	GSY        *int   `json:"gs_y,omitempty"`
	GSW        int    `json:"gs_w"`
	GSH        int    `json:"gs_h"`
	CreatedAt  string `json:"created_at,omitempty"`
	UpdatedAt  string `json:"updated_at,omitempty"`
}

// WSEvent is a WebSocket message from the backend.
type WSEvent struct {
	Type      string      `json:"type"`
	Data      interface{} `json:"data"`
	Timestamp string      `json:"timestamp,omitempty"`
}
