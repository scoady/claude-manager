---
name: deploy
description: Build, deploy, and restart the claude-manager application. Handles committing changes, pushing to GitHub, triggering Jenkins CI/CD for the Kubernetes frontend, and restarting the docker-compose backend with proper OAuth authentication.
allowed-tools: Bash, Read, Glob, Grep
argument-hint: "[commit message]"
---

# Deploy claude-manager

Deploy the claude-manager application end-to-end. This skill handles:
1. Committing and pushing code changes
2. Triggering the Jenkins CI/CD pipeline (frontend → k8s)
3. Restarting the docker-compose backend with OAuth auth

## Arguments

`$ARGUMENTS` — optional commit message. If not provided, auto-generate one from the diff.

## Steps

### 1. Check for uncommitted changes

Run `git status` and `git diff --stat` in `/Users/ayx106492/git/claude-manager`.

- If there are changes: stage them, commit with the provided message (or auto-generate), and push using `gpush` (defined in ~/.zshrc — runs git push then triggers Jenkins automatically).
- If clean: skip to step 2.
- NEVER use `git push` directly — always use `gpush`.

### 2. Trigger Jenkins build (if gpush didn't already)

The `gpush` alias normally triggers Jenkins automatically. If it didn't (or if there were no changes to push but a rebuild is needed), trigger manually:

```bash
source ~/.zshrc 2>/dev/null
COOKIE_JAR=$(mktemp)
CRUMB_JSON=$(curl -s -c "$COOKIE_JAR" -u "${JENKINS_USER}:${JENKINS_PASS}" "http://jenkins.localhost/crumbIssuer/api/json")
CRUMB_FIELD=$(echo "$CRUMB_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['crumbRequestField'])")
CRUMB_VAL=$(echo "$CRUMB_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['crumb'])")
curl -s -w "%{http_code}" -b "$COOKIE_JAR" -X POST -u "${JENKINS_USER}:${JENKINS_PASS}" \
  -H "${CRUMB_FIELD}: ${CRUMB_VAL}" \
  "http://jenkins.localhost/job/claude-manager-build/build"
rm -f "$COOKIE_JAR"
```

**IMPORTANT**: The cookie jar is required — CSRF crumbs are session-bound. Without it you get 403.

### 3. Monitor Jenkins pipeline

Poll the build until complete:

```bash
source ~/.zshrc 2>/dev/null
BUILD_NUM=$(curl -s -u "${JENKINS_USER}:${JENKINS_PASS}" "http://jenkins.localhost/job/claude-manager-build/lastBuild/api/json?tree=number" | python3 -c "import sys,json; print(json.load(sys.stdin)['number'])")

while true; do
  RESULT=$(curl -s -u "${JENKINS_USER}:${JENKINS_PASS}" "http://jenkins.localhost/job/claude-manager-build/${BUILD_NUM}/api/json?tree=building,result")
  BUILDING=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['building'])")
  if [ "$BUILDING" = "False" ]; then
    echo "$RESULT" | python3 -m json.tool
    break
  fi
  sleep 5
done
```

Then check the deploy job was triggered and succeeded:

```bash
curl -s -u "${JENKINS_USER}:${JENKINS_PASS}" "http://jenkins.localhost/job/claude-manager-deploy/lastBuild/api/json?tree=number,building,result,description" | python3 -m json.tool
```

If deploy is still running, poll it the same way.

### 4. Restart docker-compose backend

**CRITICAL**: Always use `scripts/start.sh` — NEVER raw `docker compose up`. The script extracts the Claude OAuth token from the macOS Keychain. Without it, agents get "Not logged in" errors.

```bash
cd /Users/ayx106492/git/claude-manager
docker compose down
bash scripts/start.sh -d
```

### 5. Verify

- Check the k8s pod is running the new image:
  ```bash
  kubectl get pods -n claude-manager -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.containers[0].image}{"\n"}{end}'
  ```
- Check the backend container is healthy:
  ```bash
  docker ps --filter "name=claude-manager" --format "table {{.Names}}\t{{.Status}}"
  ```

### Summary output

Report:
- Commit SHA pushed
- Jenkins build number and result
- Jenkins deploy number and result
- K8s pod name and image tag
- Backend container status
