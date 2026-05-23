# Semantic Release Pipeline — Design Spec

**Date:** 2026-05-23
**Status:** Approved

---

## Objective

Automate versioning and releases for `cpap-parser` using `semantic-release`. Merging a Conventional Commit MR into `main` automatically bumps the version, updates `CHANGELOG.md`, commits the manifest files, creates a GitLab Release with a tag, and publishes wheels to the GitLab PyPI registry — with no manual steps.

---

## Trigger Flow

```
MR merged to main
    │
    ▼
main pipeline: release job (semantic-release)
    ├── no feat/fix/BREAKING commits → no-op, pipeline ends
    └── feat/fix/BREAKING commits found
            ├── bump pyproject.toml + Cargo.toml (same version, always in sync)
            ├── write/update CHANGELOG.md
            ├── commit all three files → main  [skip ci]
            ├── create git tag (e.g. v0.2.0)
            └── create GitLab Release with generated notes
                    │
                    ▼ (GitLab auto-creates tag pipeline)
            tag pipeline: build + publish
                ├── build: maturin wheels (Python 3.11, 3.12, 3.13)
                └── publish: upload wheels to GitLab PyPI registry
```

---

## New Files

### `package.json`
Minimal Node.js manifest — declares semantic-release and its plugins. Not a build system; `version` is a placeholder so npm doesn't complain.

```json
{
  "name": "cpap-parser",
  "version": "0.0.0-semantic-release",
  "private": true,
  "devDependencies": {
    "semantic-release": "^24",
    "@semantic-release/commit-analyzer": "^13",
    "@semantic-release/release-notes-generator": "^14",
    "@semantic-release/changelog": "^6",
    "@semantic-release/exec": "^6",
    "@semantic-release/git": "^10",
    "@semantic-release/gitlab": "^13"
  }
}
```

`package-lock.json` is generated once locally (`npm install`) and committed — required for `npm ci` in CI.

### `.releaserc.json`
Plugin pipeline executed by semantic-release:

```json
{
  "branches": ["main"],
  "plugins": [
    "@semantic-release/commit-analyzer",
    "@semantic-release/release-notes-generator",
    ["@semantic-release/changelog", {
      "changelogFile": "CHANGELOG.md"
    }],
    ["@semantic-release/exec", {
      "prepareCmd": "sed -i 's/^version = \".*\"/version = \"${nextRelease.version}\"/' pyproject.toml && sed -i 's/^version = \".*\"/version = \"${nextRelease.version}\"/' Cargo.toml"
    }],
    ["@semantic-release/git", {
      "assets": ["pyproject.toml", "Cargo.toml", "CHANGELOG.md"],
      "message": "chore(release): v${nextRelease.version} [skip ci]\n\n${nextRelease.notes}"
    }],
    "@semantic-release/gitlab"
  ]
}
```

---

## CI/CD Changes (`.gitlab-ci.yml`)

### New stage
Add `release` between `secret-detection` and `publish`:
```yaml
stages:
  - build
  - test
  - secret-detection
  - release
  - publish
  - deploy
```

### New `release` job
Runs only on `main` branch pushes. Uses a Group Access Token (`$SEMANTIC_RELEASE_TOKEN`) stored in the GitLab group CI/CD variables.

```yaml
release:
  stage: release
  image: node:20-slim
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
  before_script:
    - npm ci
  script:
    - npx semantic-release
  variables:
    GITLAB_TOKEN: $SEMANTIC_RELEASE_TOKEN
    GIT_AUTHOR_NAME: "semantic-release-bot"
    GIT_AUTHOR_EMAIL: "semantic-release-bot@open-cpap.gitlab.io"
    GIT_COMMITTER_NAME: "semantic-release-bot"
    GIT_COMMITTER_EMAIL: "semantic-release-bot@open-cpap.gitlab.io"
  cache:
    key: node-modules
    paths:
      - node_modules/
```

### `publish` job change
Remove `when: manual` — the tag pipeline is the gate, not a human. Semantic-release owns all tag creation.

```yaml
publish:
  rules:
    - if: $CI_COMMIT_TAG
```

### Pipeline matrix (no change needed)
Both `build` and `publish` already carry `if: $CI_COMMIT_TAG` rules and will run correctly in the tag pipeline. No structural changes required.

---

## `.gitignore` Additions

```
node_modules/
.npm/
npm-debug.log*
```

`package-lock.json` is **not** ignored — it must be committed so `npm ci` is reproducible in CI.

---

## Renovate Changes (`renovate.json`)

Enable semantic commits globally. Default type is `chore(deps):` — no release. Production dependencies use `fix(deps):` — triggers an automated patch release.

```json
{
  "semanticCommits": "enabled",
  "semanticCommitType": "chore",
  "semanticCommitScope": "deps",
  "packageRules": [
    {
      "matchDepTypes": ["dependencies"],
      "semanticCommitType": "fix",
      "semanticCommitScope": "deps"
    }
  ]
}
```

Existing rules (automerge patches, Python dep grouping, `cpap-py` label) are preserved and merged with this.

---

## Manual Prerequisite (One-Time)

Before this pipeline works, a Group Access Token must exist with write permissions:

1. GitLab group → **Settings → Access Tokens** → create token with `api` + `write_repository` scopes
2. GitLab group → **Settings → CI/CD → Variables** → add `SEMANTIC_RELEASE_TOKEN` (masked, not protected) with the token value
3. Run `npm install` locally once → commit `package-lock.json`

---

## Commit Message → Version Bump Mapping

| Commit type | Version bump |
|---|---|
| `fix:`, `fix(scope):` | Patch (0.1.0 → 0.1.1) |
| `feat:`, `feat(scope):` | Minor (0.1.0 → 0.2.0) |
| `BREAKING CHANGE:` in footer | Major (0.1.0 → 1.0.0) |
| `chore:`, `docs:`, `ci:`, `test:` | No release |

---

## Out of Scope

- Publishing to PyPI.org (public registry) — not part of this design
- Per-adapter release channels
- Pre-release / beta channels (`next`, `alpha` branches)
