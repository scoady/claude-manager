// Package main is the entry point for the claude-manager CLI (cm).
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/charmbracelet/lipgloss"
	"github.com/scoady/claude-manager-cli/internal/api"
	"github.com/scoady/claude-manager-cli/internal/ui"
	"github.com/spf13/cobra"
)

var (
	apiURL string
	client *api.Client
)

func main() {
	rootCmd := &cobra.Command{
		Use:   "cm",
		Short: "Claude Manager CLI — manage agents, projects, and dashboards",
		Long: ui.Banner() + "\n\n" +
			"A powerful terminal interface for the Claude Agent Manager.\n" +
			"Manage projects, dispatch agents, watch live output, and inspect dashboards.",
		PersistentPreRun: func(cmd *cobra.Command, args []string) {
			if envURL := os.Getenv("CM_API_URL"); envURL != "" && apiURL == "http://localhost:4040" {
				apiURL = envURL
			}
			client = api.NewClient(apiURL)
		},
		SilenceUsage: true,
	}

	rootCmd.PersistentFlags().StringVar(&apiURL, "api", "http://localhost:4040", "Backend API URL")

	rootCmd.AddCommand(
		healthCmd(),
		statusCmd(),
		projectsCmd(),
		agentsCmd(),
		dispatchCmd(),
		watchCmd(),
		tasksCmd(),
		canvasCmd(),
	)

	if err := rootCmd.Execute(); err != nil {
		os.Exit(1)
	}
}

// ═══════════════════════════════════════════════════════════════════════════════
// cm health
// ═══════════════════════════════════════════════════════════════════════════════

func healthCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "health",
		Short: "Check backend health",
		RunE: func(cmd *cobra.Command, args []string) error {
			h, err := client.Health()
			if err != nil {
				fmt.Println(ui.ErrorBox.Render("Backend unreachable: " + err.Error()))
				return nil
			}
			stats, _ := client.Stats()

			fmt.Println(ui.Banner())
			fmt.Println()

			status := ui.SuccessText.Render("● healthy")
			if h.Status != "ok" {
				status = ui.StatusError.Render("● " + h.Status)
			}

			fmt.Println(ui.KeyValue("Status", status))
			fmt.Println(ui.KeyValue("Uptime", ui.FormatUptime(h.Uptime)))
			fmt.Println(ui.KeyValue("Agents", fmt.Sprintf("%d", h.Agents)))
			fmt.Println(ui.KeyValue("WebSocket Clients", fmt.Sprintf("%d", h.WSConnections)))

			if stats != nil {
				fmt.Println()
				fmt.Println(ui.SectionHeader("Stats"))
				fmt.Println(ui.KeyValue("Projects", fmt.Sprintf("%d", stats.TotalProjects)))
				fmt.Println(ui.KeyValue("Total Agents", fmt.Sprintf("%d", stats.TotalAgents)))
				fmt.Println(ui.KeyValue("Working", ui.StatusWorking.Render(fmt.Sprintf("%d", stats.WorkingAgents))))
				fmt.Println(ui.KeyValue("Idle", ui.StatusIdle.Render(fmt.Sprintf("%d", stats.IdleAgents))))
			}
			fmt.Println()
			return nil
		},
	}
}

// ═══════════════════════════════════════════════════════════════════════════════
// cm status
// ═══════════════════════════════════════════════════════════════════════════════

func statusCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "status",
		Short: "Rich status dashboard — projects, agents, and stats",
		RunE: func(cmd *cobra.Command, args []string) error {
			projects, err := client.GetProjects()
			if err != nil {
				fmt.Println(ui.ErrorBox.Render("Failed to fetch projects: " + err.Error()))
				return nil
			}
			agents, err := client.GetAgents()
			if err != nil {
				fmt.Println(ui.ErrorBox.Render("Failed to fetch agents: " + err.Error()))
				return nil
			}
			stats, _ := client.Stats()

			fmt.Println(ui.Banner())

			// ── Stats summary ──
			if stats != nil {
				statsLine := fmt.Sprintf(
					"%s projects  %s agents (%s working, %s idle)  %s uptime",
					ui.Bold.Render(fmt.Sprintf("%d", stats.TotalProjects)),
					ui.Bold.Render(fmt.Sprintf("%d", stats.TotalAgents)),
					ui.StatusWorking.Render(fmt.Sprintf("%d", stats.WorkingAgents)),
					ui.StatusIdle.Render(fmt.Sprintf("%d", stats.IdleAgents)),
					ui.Dim.Render(ui.FormatUptime(stats.UptimeSeconds)),
				)
				fmt.Println(ui.Dim.Render("  " + statsLine))
			}

			// ── Projects table ──
			fmt.Println(ui.SectionHeader("Projects"))

			// Build agent count map
			agentsByProject := make(map[string][]api.Agent)
			for _, a := range agents {
				agentsByProject[a.ProjectName] = append(agentsByProject[a.ProjectName], a)
			}

			var projRows [][]string
			for _, p := range projects {
				pa := agentsByProject[p.Name]
				agentCount := fmt.Sprintf("%d", len(pa))

				// Count working
				working := 0
				for _, a := range pa {
					if a.Status == "working" || a.Status == "active" {
						working++
					}
				}

				statusStr := ui.StatusIdle.Render("idle")
				if working > 0 {
					statusStr = ui.StatusWorking.Render(fmt.Sprintf("%d working", working))
				}
				if len(pa) == 0 {
					statusStr = ui.Dim.Render("no agents")
				}

				desc := p.Description
				if desc == "" && p.Goal != "" {
					// Use first line of goal
					lines := strings.SplitN(p.Goal, "\n", 2)
					desc = strings.TrimPrefix(strings.TrimSpace(lines[0]), "# ")
				}

				projRows = append(projRows, []string{
					ui.Bold.Render(p.Name),
					agentCount,
					statusStr,
					ui.Truncate(desc, 50),
				})
			}

			if len(projRows) > 0 {
				fmt.Println(ui.RenderTable(
					[]string{"Project", "Agents", "Status", "Description"},
					projRows,
				))
			} else {
				fmt.Println(ui.Dim.Render("  No projects found."))
			}

			// ── Active agents table ──
			fmt.Println(ui.SectionHeader("Active Agents"))

			var agentRows [][]string
			for _, a := range agents {
				milestone := "-"
				if len(a.Milestones) > 0 {
					milestone = ui.Truncate(a.Milestones[len(a.Milestones)-1], 40)
				}

				task := ui.Truncate(a.Task, 35)
				if a.IsController {
					task = ui.Dim.Render("[ctrl] ") + task
				}

				sid := a.SessionID
				if len(sid) > 12 {
					sid = sid[:12] + "..."
				}

				agentRows = append(agentRows, []string{
					ui.StatusIcon(a.Status) + " " + sid,
					a.ProjectName,
					ui.StatusStyle(a.Phase).Render(a.Phase),
					task,
					milestone,
					a.ElapsedString(),
				})
			}

			if len(agentRows) > 0 {
				fmt.Println(ui.RenderTable(
					[]string{"Session", "Project", "Phase", "Task", "Last Milestone", "Elapsed"},
					agentRows,
				))
			} else {
				fmt.Println(ui.Dim.Render("  No active agents."))
			}

			fmt.Println()
			return nil
		},
	}
}

// ═══════════════════════════════════════════════════════════════════════════════
// cm projects [name]
// ═══════════════════════════════════════════════════════════════════════════════

func projectsCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "projects [name]",
		Short: "List projects or show project detail",
		Args:  cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if len(args) == 1 {
				return showProjectDetail(args[0])
			}
			return listProjects()
		},
	}
}

func listProjects() error {
	projects, err := client.GetProjects()
	if err != nil {
		fmt.Println(ui.ErrorBox.Render(err.Error()))
		return nil
	}

	fmt.Println(ui.Banner())
	fmt.Println(ui.SectionHeader("Managed Projects"))

	var rows [][]string
	for _, p := range projects {
		agents := fmt.Sprintf("%d", len(p.ActiveSessionIDs))
		model := p.Config.Model
		if model == "" {
			model = ui.Dim.Render("default")
		}

		desc := p.Description
		if desc == "" {
			desc = "-"
		}

		rows = append(rows, []string{
			ui.Bold.Render(p.Name),
			agents,
			model,
			ui.Truncate(desc, 55),
		})
	}

	if len(rows) > 0 {
		fmt.Println(ui.RenderTable(
			[]string{"Project", "Agents", "Model", "Description"},
			rows,
		))
	} else {
		fmt.Println(ui.Dim.Render("  No projects."))
	}
	fmt.Println()
	return nil
}

func showProjectDetail(name string) error {
	p, err := client.GetProject(name)
	if err != nil {
		fmt.Println(ui.ErrorBox.Render(err.Error()))
		return nil
	}

	fmt.Println(ui.Banner())
	fmt.Println(ui.Title.Render("Project: " + p.Name))
	fmt.Println(ui.KeyValue("Path", p.Path))
	if p.Description != "" {
		fmt.Println(ui.KeyValue("Description", p.Description))
	}
	fmt.Println(ui.KeyValue("Parallelism", fmt.Sprintf("%d", p.Config.Parallelism)))
	model := p.Config.Model
	if model == "" {
		model = "default"
	}
	fmt.Println(ui.KeyValue("Model", model))
	fmt.Println(ui.KeyValue("Active Agents", fmt.Sprintf("%d", len(p.ActiveSessionIDs))))

	// Show agents for this project
	agents, _ := client.GetAgents()
	var projectAgents []api.Agent
	for _, a := range agents {
		if a.ProjectName == name {
			projectAgents = append(projectAgents, a)
		}
	}

	if len(projectAgents) > 0 {
		fmt.Println(ui.SectionHeader("Agents"))
		for _, a := range projectAgents {
			sid := a.SessionID
			if len(sid) > 16 {
				sid = sid[:16] + "..."
			}
			line := fmt.Sprintf("  %s %s  %s  %s",
				ui.StatusIcon(a.Status),
				ui.Bold.Render(sid),
				ui.StatusStyle(a.Phase).Render(a.Phase),
				ui.Dim.Render(a.ElapsedString()),
			)
			fmt.Println(line)
			if a.Task != "" {
				fmt.Println("    " + ui.Truncate(a.Task, 70))
			}
			if len(a.Milestones) > 0 {
				last := a.Milestones[len(a.Milestones)-1]
				fmt.Println("    " + ui.Dim.Render("last: "+last))
			}
		}
	}

	// Show tasks
	tasks, _ := client.GetTasks(name)
	if len(tasks) > 0 {
		fmt.Println(ui.SectionHeader("Tasks"))
		for _, t := range tasks {
			fmt.Printf("  %s %s\n", ui.TaskStatusIcon(t.Status), t.Text)
		}
	}

	fmt.Println()
	return nil
}

// ═══════════════════════════════════════════════════════════════════════════════
// cm agents [--active] / cm agents stop <session_id>
// ═══════════════════════════════════════════════════════════════════════════════

func agentsCmd() *cobra.Command {
	var activeOnly bool

	cmd := &cobra.Command{
		Use:   "agents",
		Short: "List agents or manage them",
		RunE: func(cmd *cobra.Command, args []string) error {
			agents, err := client.GetAgents()
			if err != nil {
				fmt.Println(ui.ErrorBox.Render(err.Error()))
				return nil
			}

			if activeOnly {
				var filtered []api.Agent
				for _, a := range agents {
					if a.Status == "working" || a.Status == "active" {
						filtered = append(filtered, a)
					}
				}
				agents = filtered
			}

			fmt.Println(ui.Banner())
			fmt.Println(ui.SectionHeader("Agents"))

			if len(agents) == 0 {
				label := "No agents"
				if activeOnly {
					label = "No active agents"
				}
				fmt.Println(ui.Dim.Render("  " + label + "."))
				fmt.Println()
				return nil
			}

			var rows [][]string
			for _, a := range agents {
				sid := a.SessionID
				if len(sid) > 16 {
					sid = sid[:16] + "..."
				}

				task := ui.Truncate(a.Task, 40)
				model := a.Model
				if model == "" {
					model = "-"
				}

				rows = append(rows, []string{
					ui.StatusIcon(a.Status) + " " + sid,
					a.ProjectName,
					ui.StatusStyle(a.Status).Render(a.Status),
					ui.StatusStyle(a.Phase).Render(a.Phase),
					task,
					model,
					fmt.Sprintf("%d", a.TurnCount),
					a.ElapsedString(),
				})
			}

			fmt.Println(ui.RenderTable(
				[]string{"Session", "Project", "Status", "Phase", "Task", "Model", "Turns", "Elapsed"},
				rows,
			))
			fmt.Println()
			return nil
		},
	}

	cmd.Flags().BoolVar(&activeOnly, "active", false, "Show only active/working agents")

	// Sub-command: cm agents stop <session_id>
	stopCmd := &cobra.Command{
		Use:   "stop <session_id>",
		Short: "Stop an agent by session ID",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			sessionID := args[0]
			err := client.KillAgent(sessionID)
			if err != nil {
				fmt.Println(ui.ErrorBox.Render(err.Error()))
				return nil
			}
			fmt.Println(ui.SuccessText.Render("Agent " + sessionID + " stopped."))
			return nil
		},
	}
	cmd.AddCommand(stopCmd)

	return cmd
}

// ═══════════════════════════════════════════════════════════════════════════════
// cm dispatch <project> "<task>" [--follow] [--model <model>]
// ═══════════════════════════════════════════════════════════════════════════════

func dispatchCmd() *cobra.Command {
	var follow bool
	var model string

	cmd := &cobra.Command{
		Use:   "dispatch <project> <task>",
		Short: "Dispatch a task to a project",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			project := args[0]
			task := args[1]

			fmt.Printf("%s Dispatching to %s...\n",
				ui.StatusWorking.Render("●"),
				ui.Bold.Render(project),
			)

			resp, err := client.Dispatch(project, task, model)
			if err != nil {
				fmt.Println(ui.ErrorBox.Render(err.Error()))
				return nil
			}

			if resp.SessionID != "" {
				fmt.Println(ui.SuccessText.Render("Agent spawned: ") + ui.Bold.Render(resp.SessionID))
			}
			if len(resp.AgentIDs) > 0 {
				for _, id := range resp.AgentIDs {
					fmt.Println(ui.SuccessText.Render("  Agent: ") + ui.Bold.Render(id))
				}
			}
			if resp.Status != "" {
				fmt.Println(ui.KeyValue("Status", resp.Status))
			}

			if follow {
				fmt.Println()
				fmt.Println(ui.Dim.Render("Connecting to live stream..."))
				return streamWatch(project, "")
			}

			return nil
		},
	}

	cmd.Flags().BoolVarP(&follow, "follow", "f", false, "Follow agent output after dispatch")
	cmd.Flags().StringVar(&model, "model", "", "Override model for this dispatch")

	return cmd
}

// ═══════════════════════════════════════════════════════════════════════════════
// cm watch <session_id|project>
// ═══════════════════════════════════════════════════════════════════════════════

func watchCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "watch <session_id or project_name>",
		Short: "Stream live agent output via WebSocket",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			target := args[0]

			// Determine if target is a project name or session ID
			// Session IDs are typically UUIDs or "pending-xxx" format
			isProject := !strings.Contains(target, "-") || len(target) < 20

			// Try to resolve as project first
			if isProject {
				_, err := client.GetProject(target)
				if err == nil {
					fmt.Printf("%s Watching project %s (Ctrl+C to stop)\n\n",
						ui.StatusIdle.Render("◉"),
						ui.Bold.Render(target),
					)
					return streamWatch(target, "")
				}
			}

			fmt.Printf("%s Watching session %s (Ctrl+C to stop)\n\n",
				ui.StatusIdle.Render("◉"),
				ui.Bold.Render(target),
			)
			return streamWatch("", target)
		},
	}
}

func streamWatch(projectFilter, sessionFilter string) error {
	// Style helpers
	milestoneStyle := lipgloss.NewStyle().
		Foreground(ui.ColorCyan).
		Bold(true)
	phaseStyle := lipgloss.NewStyle().
		Foreground(ui.ColorPurple).
		Bold(true)
	textStyle := lipgloss.NewStyle().
		Foreground(ui.ColorWhite)
	dimStyle := lipgloss.NewStyle().
		Foreground(ui.ColorDim)
	agentLabel := lipgloss.NewStyle().
		Foreground(ui.ColorAmber).
		Bold(true)
	errorStyle := lipgloss.NewStyle().
		Foreground(ui.ColorRed).
		Bold(true)

	return client.StreamWebSocket(func(event api.WSEvent) error {
		dataMap, ok := event.Data.(map[string]interface{})
		if !ok {
			return nil
		}

		// Filter by project or session
		if projectFilter != "" {
			if pn, ok := dataMap["project_name"].(string); ok && pn != projectFilter {
				return nil
			}
			// Also check nested project field
			if pn, ok := dataMap["project"].(string); ok && pn != projectFilter {
				return nil
			}
		}
		if sessionFilter != "" {
			if sid, ok := dataMap["session_id"].(string); ok && sid != sessionFilter {
				return nil
			}
		}

		ts := time.Now().Format("15:04:05")
		prefix := dimStyle.Render(ts)

		switch event.Type {
		case "agent_spawned":
			sid, _ := dataMap["session_id"].(string)
			task, _ := dataMap["task"].(string)
			fmt.Printf("%s %s Agent spawned %s\n", prefix,
				agentLabel.Render("⚡"),
				ui.Bold.Render(truncateStr(sid, 20)),
			)
			if task != "" {
				fmt.Printf("  %s\n", dimStyle.Render(truncateStr(task, 80)))
			}

		case "agent_milestone":
			label, _ := dataMap["label"].(string)
			if label == "" {
				label, _ = dataMap["milestone"].(string)
			}
			sid, _ := dataMap["session_id"].(string)
			shortSID := truncateStr(sid, 8)
			fmt.Printf("%s %s %s\n", prefix,
				dimStyle.Render("["+shortSID+"]"),
				milestoneStyle.Render(label),
			)

		case "session_phase":
			phase, _ := dataMap["phase"].(string)
			sid, _ := dataMap["session_id"].(string)
			shortSID := truncateStr(sid, 8)
			fmt.Printf("%s %s phase → %s\n", prefix,
				dimStyle.Render("["+shortSID+"]"),
				phaseStyle.Render(phase),
			)

		case "agent_stream":
			text, _ := dataMap["text"].(string)
			if text != "" {
				// Stream text deltas
				fmt.Print(textStyle.Render(text))
			}

		case "tool_start":
			name, _ := dataMap["name"].(string)
			sid, _ := dataMap["session_id"].(string)
			shortSID := truncateStr(sid, 8)
			// Render as a badge
			badge := ui.Badge.Render(name)
			inputStr := ""
			if inp, ok := dataMap["input"].(map[string]interface{}); ok {
				// Show key input fields
				if fp, ok := inp["file_path"].(string); ok {
					inputStr = " " + dimStyle.Render(fp)
				} else if cmd, ok := inp["command"].(string); ok {
					inputStr = " " + dimStyle.Render(truncateStr(cmd, 50))
				} else if p, ok := inp["pattern"].(string); ok {
					inputStr = " " + dimStyle.Render(p)
				}
			}
			fmt.Printf("\n%s %s %s%s\n", prefix,
				dimStyle.Render("["+shortSID+"]"),
				badge, inputStr,
			)

		case "tool_done":
			// Brief acknowledgment
			name, _ := dataMap["name"].(string)
			_ = name // could show duration here

		case "agent_done":
			sid, _ := dataMap["session_id"].(string)
			fmt.Printf("\n%s %s Agent done %s\n", prefix,
				ui.SuccessText.Render("✓"),
				ui.Bold.Render(truncateStr(sid, 20)),
			)

		case "error":
			msg, _ := dataMap["message"].(string)
			if msg == "" {
				msg, _ = dataMap["error"].(string)
			}
			fmt.Printf("%s %s %s\n", prefix,
				errorStyle.Render("ERROR"),
				msg,
			)

		case "agent_state_sync":
			// Initial state dump on connect
			if agentsRaw, ok := dataMap["agents"].([]interface{}); ok {
				count := len(agentsRaw)
				if count > 0 {
					fmt.Printf("%s %s Connected — %d agent(s) active\n", prefix,
						ui.SuccessText.Render("●"),
						count,
					)
				} else {
					fmt.Printf("%s %s Connected — no active agents\n", prefix,
						dimStyle.Render("●"),
					)
				}
			}

		case "stats_update", "project_list", "pong":
			// Silently ignore these
		}

		return nil
	})
}

func truncateStr(s string, max int) string {
	if len(s) <= max {
		return s
	}
	if max < 4 {
		return s[:max]
	}
	return s[:max-3] + "..."
}

// ═══════════════════════════════════════════════════════════════════════════════
// cm tasks <project>
// ═══════════════════════════════════════════════════════════════════════════════

func tasksCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "tasks <project>",
		Short: "List tasks for a project",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			project := args[0]
			tasks, err := client.GetTasks(project)
			if err != nil {
				fmt.Println(ui.ErrorBox.Render(err.Error()))
				return nil
			}

			fmt.Println(ui.Banner())
			fmt.Println(ui.SectionHeader("Tasks — " + project))

			if len(tasks) == 0 {
				fmt.Println(ui.Dim.Render("  No tasks."))
				fmt.Println()
				return nil
			}

			var rows [][]string
			for i, t := range tasks {
				rows = append(rows, []string{
					fmt.Sprintf("%d", i),
					ui.TaskStatusIcon(t.Status) + " " + ui.StatusStyle(t.Status).Render(t.Status),
					t.Text,
				})
			}

			fmt.Println(ui.RenderTable(
				[]string{"#", "Status", "Task"},
				rows,
			))
			fmt.Println()
			return nil
		},
	}
}

// ═══════════════════════════════════════════════════════════════════════════════
// cm canvas <project> / cm canvas put ... / cm canvas rm ...
// ═══════════════════════════════════════════════════════════════════════════════

func canvasCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "canvas <project>",
		Short: "List or manage canvas widgets",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			project := args[0]
			widgets, err := client.GetWidgets(project)
			if err != nil {
				fmt.Println(ui.ErrorBox.Render(err.Error()))
				return nil
			}

			fmt.Println(ui.Banner())
			fmt.Println(ui.SectionHeader("Canvas Widgets — " + project))

			if len(widgets) == 0 {
				fmt.Println(ui.Dim.Render("  No widgets."))
				fmt.Println()
				return nil
			}

			var rows [][]string
			for _, w := range widgets {
				pos := "-"
				if w.GSX != nil && w.GSY != nil {
					pos = fmt.Sprintf("(%d,%d)", *w.GSX, *w.GSY)
				}
				tmpl := w.TemplateID
				if tmpl == "" {
					tmpl = "-"
				}

				rows = append(rows, []string{
					ui.Bold.Render(truncateStr(w.ID, 20)),
					w.Title,
					tmpl,
					fmt.Sprintf("%dx%d", w.GSW, w.GSH),
					pos,
					w.Tab,
				})
			}

			fmt.Println(ui.RenderTable(
				[]string{"ID", "Title", "Template", "Size", "Position", "Tab"},
				rows,
			))
			fmt.Println()
			return nil
		},
	}

	// cm canvas put <project> --template <id> --data '{...}'
	var template string
	var data string
	putCmd := &cobra.Command{
		Use:   "put <project>",
		Short: "Create a widget on the canvas",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			project := args[0]

			body := map[string]interface{}{}
			if template != "" {
				body["template_id"] = template
			}
			if data != "" {
				var parsed map[string]interface{}
				if err := json.Unmarshal([]byte(data), &parsed); err != nil {
					fmt.Println(ui.ErrorBox.Render("Invalid JSON data: " + err.Error()))
					return nil
				}
				body["template_data"] = parsed
			}

			w, err := client.CreateWidget(project, body)
			if err != nil {
				fmt.Println(ui.ErrorBox.Render(err.Error()))
				return nil
			}

			fmt.Println(ui.SuccessText.Render("Widget created: ") + ui.Bold.Render(w.ID))
			return nil
		},
	}
	putCmd.Flags().StringVar(&template, "template", "", "Widget template ID")
	putCmd.Flags().StringVar(&data, "data", "", "Widget data as JSON string")

	// cm canvas rm <project> <widget_id>
	rmCmd := &cobra.Command{
		Use:   "rm <project> <widget_id>",
		Short: "Remove a widget from the canvas",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			project := args[0]
			widgetID := args[1]

			err := client.DeleteWidget(project, widgetID)
			if err != nil {
				fmt.Println(ui.ErrorBox.Render(err.Error()))
				return nil
			}

			fmt.Println(ui.SuccessText.Render("Widget removed: ") + widgetID)
			return nil
		},
	}

	cmd.AddCommand(putCmd, rmCmd)
	return cmd
}
