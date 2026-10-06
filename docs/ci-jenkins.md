# CI with Jenkins + SonarQube

Every push to `main` on GitHub (`Jeziel-ent/signage-automation-tool`) is picked up by Jenkins, which runs the tests, publishes
coverage to SonarQube and fails the build when the SonarQube quality gate is not OK. `Jenkinsfile` is the pipeline;
`ci/` holds three small helpers it calls.

```
push -> Jenkins polls GitHub (every ~2 min)
     -> Backend tests        venv (Python 3.10), pytest + coverage     (mock engine, throwaway data dir - never CorelDRAW)
     -> Frontend tests       npm ci, node tests + lcov, vite build
     -> SonarQube analysis   scan (reads sonar-project.properties)
     -> Quality gate report  ci/wait_quality_gate.py: waits for SonarQube, writes ci-reports/, fails the build when the gate is ERROR
```

The last stage produces the **quality gate report**: `ci-reports/quality-gate-report.md` (gate conditions + bugs, vulnerabilities, code smells,
coverage, duplication), the same data as `.json`, and a one-line summary that Jenkins shows as the build description. All three are
archived with the build.

## One-time setup (Windows)

**Tools on the machine that runs the build** - Jenkins must run as a normal user that can see them (not as `SYSTEM`):
Python 3.10 (`py -3.10` works), Node 22+ and npm, JDK 21 (Jenkins and the scanner), Git. SonarQube Community Build on
`http://localhost:9000` (see `sonar-project.properties`).

**1. Jenkins.** Run the LTS war as the current user: `java -jar jenkins.war --httpPort=7070` with `JENKINS_HOME` set to a folder
you own (for example `D:\jenkins_home`). Open http://localhost:7070, unlock with
`<JENKINS_HOME>\secrets\initialAdminPassword`, choose "Install suggested plugins" (Git, Pipeline, Credentials Binding, JUnit and
Timestamper are all in that set) and create your admin user.

**2. SonarQube token.** In SonarQube: avatar -> My Account -> Security -> generate a token named `jenkins`.
In Jenkins: Manage Jenkins -> Credentials -> (global) -> Add -> kind **Secret text**, ID **`sonar-token`**, paste the token.
Never commit it; the pipeline reads it with `withCredentials`.

**3. GitHub access (only if the repository is private).** Create a fine-grained personal access token with read-only
"Contents" access to this repository. In Jenkins add it as **Username with password** (ID `github-pat`, username = your GitHub
user, password = the token).

**4. The job.** New Item -> **Pipeline** -> Pipeline script from SCM -> Git:
repository `https://github.com/Jeziel-ent/signage-automation-tool.git`, the credential from step 3 (if private), branch
`*/main`, script path `Jenkinsfile`. Save and press **Build Now** once - that registers the 2-minute SCM poll declared in the
`Jenkinsfile` (`pollSCM('H/2 * * * *')`).

## What a build checks

| Stage | Fails when |
|---|---|
| Backend tests | any pytest test fails |
| Frontend tests | a node test fails, or `vite build` fails |
| SonarQube analysis | the scanner cannot reach SonarQube / the token is wrong |
| Quality gate report | the gate is ERROR (for example new-code coverage below 80 % or any new issue) |

Test results appear under the build's "Test Result"; `coverage.xml` and `lcov.info` are archived.

## Notes and limits

* **Real time = polling.** A push is built within about 2 minutes. GitHub cannot reach a Jenkins on `localhost`, so a webhook
  needs a public tunnel; polling needs none. To switch later, add the webhook and remove `pollSCM`.
* **SonarQube Community Build analyses one branch (`main`).** A pull-request/branch job would run the tests but its scan would
  overwrite `main`'s analysis - keep this job on `main`.
* **No live CorelDRAW in CI.** `SIGNAGE_ENGINE=mock`; tests that need a real CorelDRAW are skipped (37 as of the last run).
  Layout changes still need a visual check on real boards - CI proves the API, the logic and the UI build, not the artwork.
* The first backend run installs all of `requirements.txt` into `backend\.venv` inside the workspace (a few minutes); later
  runs reuse it.
* Run the pieces by hand: `node ci/frontend_coverage.mjs` (from `frontend/`), `py -3.10 ci/prepare_lcov.py`,
  `npx @sonar/scan -Dsonar.host.url=http://localhost:9000`, `py -3.10 ci/wait_quality_gate.py` (needs `SONAR_TOKEN`).
