// ci/deploy.Jenkinsfile
// Parameterized pipeline: runs `helm upgrade` to deploy claude-manager with
// the specified image tag from the in-cluster registry.
//
// Triggered automatically by ci/build.Jenkinsfile, or manually from the
// Jenkins UI with any tag.
//
// Requires agent label 'helm' (alpine/helm + kubectl):
//   - jnlp container: clones repo
//   - helm container: runs helm upgrade and kubectl rollout status
//
// NOTE: The backend pod runs with hostPID=true and a hostPath volume mount
// for ~/.claude so it can inspect the host's Claude agent processes and
// session files. Ensure the node has the claude CLI installed at the path
// configured in values.yaml (backend.claudeBinPath).

def REGISTRY = "registry.registry.svc.cluster.local:5000"

pipeline {
  agent { label 'helm' }

  parameters {
    string(
      name: 'IMAGE_TAG',
      defaultValue: 'latest',
      description: 'Image tag to deploy — git SHA (e.g. a1b2c3d) or SHA-dev'
    )
  }

  options {
    timeout(time: 15, unit: 'MINUTES')
    buildDiscarder(logRotator(numToKeepStr: '20'))
    disableConcurrentBuilds()
  }

  stages {

    stage('Validate') {
      steps {
        script {
          if (!params.IMAGE_TAG?.trim()) {
            error("IMAGE_TAG parameter is required")
          }
          echo "Deploying claude-manager tag=${params.IMAGE_TAG} from registry=${REGISTRY}"
          currentBuild.description = "tag=${params.IMAGE_TAG}"
        }
      }
    }

    // ── Helm upgrade ──────────────────────────────────────────────────────────
    stage('Helm upgrade') {
      steps {
        container('helm') {
          sh """
            helm upgrade --install claude-manager \\
              ${WORKSPACE}/infrastructure/helm/claude-manager \\
              --namespace claude-manager \\
              --create-namespace \\
              --values ${WORKSPACE}/infrastructure/helm/claude-manager/values.yaml \\
              --set global.imageRegistry=${REGISTRY} \\
              --set backend.image.tag=${params.IMAGE_TAG} \\
              --set backend.image.pullPolicy=Always \\
              --set frontend.image.tag=${params.IMAGE_TAG} \\
              --set frontend.image.pullPolicy=Always \\
              --wait \\
              --timeout 5m
          """
        }
      }
    }

    stage('Verify rollout') {
      steps {
        container('helm') {
          sh """
            kubectl rollout status deployment/backend  -n claude-manager --timeout=120s
            kubectl rollout status deployment/frontend -n claude-manager --timeout=120s
          """
        }
      }
    }

  }

  post {
    success {
      echo "claude-manager deployed successfully. Tag: ${params.IMAGE_TAG}"
    }
    failure {
      echo "Deployment failed for tag=${params.IMAGE_TAG}."
      sh "helm history claude-manager -n claude-manager --max 5 || true"
    }
  }
}
