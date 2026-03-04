---
name: release
description: Create a versioned release with auto-generated release notes, git tag, and GitHub release. Use when cutting a new version of claude-manager.
allowed-tools: Bash, Read, Glob, Grep
argument-hint: "<version> (e.g. v1.2.0)"
---

# Release claude-manager

Create a tagged release with generated release notes.

## Arguments

`$ARGUMENTS` — the version tag (e.g. `v1.2.0`). Required.

If no version is provided, ask the user what version to tag.

## Steps

### 1. Validate

- Ensure we're on the `main` branch
- Ensure the working tree is clean (no uncommitted changes). If there are changes, ask the user if they want to commit first (suggest using `/deploy`).
- Ensure the tag doesn't already exist: `git tag -l "$VERSION"`
- Find the previous tag: `git describe --tags --abbrev=0 2>/dev/null`

### 2. Generate release notes

Get the commit log since the last tag:

```bash
PREV_TAG=$(git describe --tags --abbrev=0 2>/dev/null || git rev-list --max-parents=0 HEAD)
git log "${PREV_TAG}..HEAD" --oneline --no-merges
```

From this log, write release notes in this format:

```markdown
## What's Changed

### Features
- <feature description> (<short sha>)

### Fixes
- <fix description> (<short sha>)

### Infrastructure
- <infra/CI/deploy changes> (<short sha>)

**Full Changelog**: <prev_tag>...<new_tag>
```

Categorize commits by reading their messages:
- "Add", "new", "feature", "implement" → Features
- "Fix", "bug", "patch", "resolve" → Fixes
- "CI", "deploy", "helm", "docker", "jenkins", "infra" → Infrastructure
- Everything else → use your judgement

Omit empty categories. Write concise, human-readable descriptions (don't just copy commit messages verbatim — clean them up).

### 3. Create the tag

```bash
git tag -a "$VERSION" -m "Release $VERSION"
```

### 4. Push tag and code

**ALWAYS use `gpush`** (never raw `git push`). Push the tag separately:

```bash
gpush
gpush --tags
```

### 5. Create GitHub release

Use `gh` CLI to create the release with the generated notes:

```bash
gh release create "$VERSION" --title "$VERSION" --notes "$(cat <<'EOF'
<release notes here>
EOF
)"
```

### 6. Summary

Report:
- Version tagged
- Commits included (count)
- GitHub release URL
- Remind user to run `/deploy` if they want to deploy this release to k8s
