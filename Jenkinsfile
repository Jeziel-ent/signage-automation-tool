// CI for the signage automation tool. A push to GitHub triggers:
//   backend tests -> frontend tests -> SonarQube analysis -> quality gate report
// Runs on a WINDOWS Jenkins agent (the backend needs pywin32). It never drives CorelDRAW: SIGNAGE_ENGINE=mock, a throwaway data directory.
// Setup (Jenkins, plugins, credentials, job): docs/ci-jenkins.md
pipeline {
    agent any

    options {
        timestamps()
        disableConcurrentBuilds()                 // one build at a time: the SonarQube project and the scratch data dir are shared
        timeout(time: 45, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '30'))
    }

    triggers {
        pollSCM('H/2 * * * *')                    // no public URL needed: GitHub is polled about every 2 minutes
    }

    environment {
        SIGNAGE_ENGINE   = 'mock'                 // never launch CorelDRAW from CI
        SIGNAGE_DATA     = "${env.WORKSPACE}\\.ci-data"
        PYTHONIOENCODING = 'utf-8'
        SONAR_HOST_URL   = 'http://localhost:9000'
        PY               = 'py -3.10'             // the project targets Python 3.10 (CLAUDE.md "Set up")
    }

    stages {
        stage('Backend tests') {
            steps {
                dir('backend') {
                    bat '''
                        if not exist .venv\\Scripts\\python.exe %PY% -m venv .venv
                        .venv\\Scripts\\python -m pip install --quiet --upgrade pip
                        .venv\\Scripts\\python -m pip install --quiet -r requirements.txt
                        .venv\\Scripts\\python -m pytest -q -p no:cacheprovider --cov=app --cov=tools --cov-report=xml:coverage.xml --junitxml=pytest-report.xml
                    '''
                }
            }
            post {
                always { junit allowEmptyResults: true, testResults: 'backend/pytest-report.xml' }
            }
        }

        stage('Frontend tests') {
            steps {
                dir('frontend') {
                    bat 'npm ci --no-audit --no-fund'
                    bat 'node ..\\ci\\frontend_coverage.mjs'      // npm test's file list + lcov output (frontend/lcov.info)
                    bat 'npx vite build'                          // the production build must still compile
                }
                bat '%PY% ci\\prepare_lcov.py'                    // repo-root-relative paths, as sonar-project.properties expects
            }
        }

        stage('SonarQube analysis') {
            steps {
                withCredentials([string(credentialsId: 'sonar-token', variable: 'SONAR_TOKEN')]) {
                    // sonar-project.properties in the repo root holds the project key, sources, exclusions and report paths
                    bat 'npx --yes @sonar/scan -Dsonar.host.url=%SONAR_HOST_URL%'
                }
            }
        }

        stage('Quality gate report') {
            steps {
                withCredentials([string(credentialsId: 'sonar-token', variable: 'SONAR_TOKEN')]) {
                    // waits for SonarQube to process the analysis, writes ci-reports/, and FAILS the build when the gate is ERROR
                    bat '%PY% ci\\wait_quality_gate.py'
                }
            }
        }
    }

    post {
        always {
            script {
                if (fileExists('ci-reports/summary.txt')) {
                    currentBuild.description = readFile('ci-reports/summary.txt').trim()   // shown on the build page
                }
            }
            archiveArtifacts artifacts: 'ci-reports/**,backend/coverage.xml,frontend/lcov.info', allowEmptyArchive: true
        }
        success { echo "Pipeline OK - dashboard: ${env.SONAR_HOST_URL}/dashboard?id=signage-automation-tool" }
        failure { echo 'Pipeline FAILED - see the first red stage (backend tests, frontend tests, analysis or the quality gate report).' }
    }
}
