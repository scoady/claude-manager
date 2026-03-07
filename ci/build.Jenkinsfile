// ci/build.Jenkinsfile
// Builds the frontend AND backend images with Kaniko, pushes them to the
// in-cluster Docker registry, then triggers the deploy pipeline.

def REGISTRY = "registry.registry.svc.cluster.local:5000"
def IMAGE_TAG = ""

pipeline {
  agent { label 'kaniko' }

  options {
    timeout(time: 30, unit: 'MINUTES')
    buildDiscarder(logRotator(numToKeepStr: '10'))
    disableConcurrentBuilds()
  }

  stages {

    // ── Stage 1: Compute image tag ────────────────────────────────────────────
    stage('Tag') {
      steps {
        script {
          def sha = sh(
            script: "git rev-parse --short HEAD",
            returnStdout: true
          ).trim()
          def dirty = sh(
            script: "git status --porcelain 2>/dev/null | wc -l | tr -d ' '",
            returnStdout: true
          ).trim()
          IMAGE_TAG = (dirty != "0") ? "${sha}-dev" : sha
          echo "Image tag: ${IMAGE_TAG}"
          currentBuild.description = "tag=${IMAGE_TAG}"
        }
      }
    }

    // ── Stage 2: Build and push images (parallel) ─────────────────────────────
    stage('Build images') {
      parallel {
        stage('Frontend') {
          steps {
            container('kaniko') {
              sh """
                /kaniko/executor \\
                  --dockerfile=${WORKSPACE}/frontend/Dockerfile \\
                  --context=dir://${WORKSPACE}/frontend \\
                  --destination=${REGISTRY}/claude-manager/frontend:${IMAGE_TAG} \\
                  --destination=${REGISTRY}/claude-manager/frontend:latest \\
                  --insecure \\
                  --insecure-pull \\
                  --skip-tls-verify \\
                  --skip-tls-verify-pull \\
                  --cache=false \\
                  --verbosity=info
              """
            }
          }
        }
        stage('Backend') {
          steps {
            container('kaniko') {
              sh """
                /kaniko/executor \\
                  --dockerfile=${WORKSPACE}/backend/Dockerfile \\
                  --context=dir://${WORKSPACE} \\
                  --destination=${REGISTRY}/claude-manager/backend:${IMAGE_TAG} \\
                  --destination=${REGISTRY}/claude-manager/backend:latest \\
                  --insecure \\
                  --insecure-pull \\
                  --skip-tls-verify \\
                  --skip-tls-verify-pull \\
                  --cache=false \\
                  --verbosity=info
              """
            }
          }
        }
      }
    }

    // ── Stage 3: Trigger deploy pipeline ──────────────────────────────────────
    stage('Deploy') {
      steps {
        script {
          echo "Triggering claude-manager-deploy with IMAGE_TAG=${IMAGE_TAG}"
          build(
            job: 'claude-manager-deploy',
            parameters: [
              string(name: 'IMAGE_TAG', value: IMAGE_TAG)
            ],
            wait: true,
            propagate: true
          )
        }
      }
    }

  }

  post {
    success {
      echo "Build complete. Images: ${REGISTRY}/claude-manager/*:${IMAGE_TAG}"
    }
    failure {
      echo "Build failed — review Kaniko output above for details."
    }
  }
}
