// Package ui provides terminal styling for the claude-manager CLI.
package ui

import (
	"fmt"
	"strings"

	"github.com/charmbracelet/lipgloss"
)

// ── Color palette ────────────────────────────────────────────────────────────

var (
	ColorCyan    = lipgloss.Color("#00d4ff")
	ColorGreen   = lipgloss.Color("#00ff88")
	ColorAmber   = lipgloss.Color("#ffaa00")
	ColorRed     = lipgloss.Color("#ff4466")
	ColorPurple  = lipgloss.Color("#aa88ff")
	ColorDim     = lipgloss.Color("#666666")
	ColorWhite   = lipgloss.Color("#eeeeee")
	ColorBorder  = lipgloss.Color("#444466")
	ColorMagenta = lipgloss.Color("#ff66ff")
)

// ── Reusable styles ─────────────────────────────────────────────────────────

var (
	// Title is a bold header style.
	Title = lipgloss.NewStyle().
		Bold(true).
		Foreground(ColorCyan).
		MarginBottom(1)

	// Subtitle is a dimmed label.
	Subtitle = lipgloss.NewStyle().
			Foreground(ColorDim).
			Italic(true)

	// Bold white text.
	Bold = lipgloss.NewStyle().Bold(true).Foreground(ColorWhite)

	// Dim is subtle text.
	Dim = lipgloss.NewStyle().Foreground(ColorDim)

	// StatusWorking renders "working" status.
	StatusWorking = lipgloss.NewStyle().
			Foreground(ColorAmber).
			Bold(true)

	// StatusIdle renders "idle" status.
	StatusIdle = lipgloss.NewStyle().
			Foreground(ColorCyan)

	// StatusDone renders "done" status.
	StatusDone = lipgloss.NewStyle().
			Foreground(ColorGreen)

	// StatusError renders "error" / "disconnected" status.
	StatusError = lipgloss.NewStyle().
			Foreground(ColorRed).
			Bold(true)

	// Badge renders a tool-call badge like [Read].
	Badge = lipgloss.NewStyle().
		Background(lipgloss.Color("#333355")).
		Foreground(ColorCyan).
		Padding(0, 1).
		Bold(true)

	// Box renders a bordered box.
	Box = lipgloss.NewStyle().
		Border(lipgloss.RoundedBorder()).
		BorderForeground(ColorBorder).
		Padding(0, 1)

	// ErrorBox renders an error panel.
	ErrorBox = lipgloss.NewStyle().
			Border(lipgloss.RoundedBorder()).
			BorderForeground(ColorRed).
			Foreground(ColorRed).
			Padding(0, 1)

	// SuccessText renders green text.
	SuccessText = lipgloss.NewStyle().Foreground(ColorGreen)

	// TableHeader style.
	TableHeader = lipgloss.NewStyle().
			Bold(true).
			Foreground(ColorPurple).
			BorderBottom(true).
			BorderStyle(lipgloss.NormalBorder()).
			BorderForeground(ColorBorder)
)

// ── Helper functions ─────────────────────────────────────────────────────────

// StatusStyle returns the appropriate style for an agent status string.
func StatusStyle(status string) lipgloss.Style {
	switch strings.ToLower(status) {
	case "working", "active":
		return StatusWorking
	case "idle":
		return StatusIdle
	case "done", "complete":
		return StatusDone
	case "error", "disconnected", "cancelled":
		return StatusError
	default:
		return Dim
	}
}

// StatusIcon returns a colored icon for an agent status.
func StatusIcon(status string) string {
	style := StatusStyle(status)
	switch strings.ToLower(status) {
	case "working", "active":
		return style.Render("●")
	case "idle":
		return style.Render("◌")
	case "done", "complete":
		return style.Render("✓")
	case "error", "disconnected", "cancelled":
		return style.Render("✗")
	default:
		return style.Render("?")
	}
}

// TaskStatusIcon returns a colored icon for a task status.
func TaskStatusIcon(status string) string {
	switch strings.ToLower(status) {
	case "done", "complete", "completed":
		return StatusDone.Render("✓")
	case "in_progress", "in-progress", "running":
		return StatusWorking.Render("▶")
	case "pending":
		return Dim.Render("○")
	default:
		return Dim.Render("?")
	}
}

// Truncate truncates a string to max length, adding "..." if needed.
func Truncate(s string, max int) string {
	if len(s) <= max {
		return s
	}
	if max < 4 {
		return s[:max]
	}
	return s[:max-3] + "..."
}

// Banner renders the CM header banner.
func Banner() string {
	logo := lipgloss.NewStyle().
		Bold(true).
		Foreground(ColorCyan).
		Render("⋆ Claude Manager")
	return logo
}

// SectionHeader renders a styled section header.
func SectionHeader(title string) string {
	return lipgloss.NewStyle().
		Bold(true).
		Foreground(ColorPurple).
		MarginTop(1).
		Render("━━ " + title + " ━━")
}

// KeyValue renders a key: value pair with styling.
func KeyValue(key, value string) string {
	return fmt.Sprintf("%s %s",
		Dim.Render(key+":"),
		Bold.Render(value),
	)
}

// FormatUptime converts seconds to a human-friendly string.
func FormatUptime(seconds float64) string {
	if seconds < 60 {
		return fmt.Sprintf("%.0fs", seconds)
	}
	if seconds < 3600 {
		return fmt.Sprintf("%.0fm%.0fs", seconds/60, float64(int(seconds)%60))
	}
	hours := int(seconds) / 3600
	mins := (int(seconds) % 3600) / 60
	return fmt.Sprintf("%dh%dm", hours, mins)
}
