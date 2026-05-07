# Contributing to mariner-app

mariner-app is an [Arches](https://www.archesproject.org/) application developed and maintained by [Historic England](https://historicengland.org.uk/) primarily for our own internal systems. The codebase is published openly in the spirit of transparency and in case it is useful to others in the Arches community.

Please read this document before raising issues or submitting pull requests.

---

## Table of Contents

- [Project Status & Scope](#project-status--scope)
- [Reporting Bugs](#reporting-bugs)
- [Feature Requests](#feature-requests)
- [Pull Requests](#pull-requests)
- [Development Setup](#development-setup)
- [Code Style](#code-style)

---

## Project Status & Scope

This repository is **actively developed for Historic England's internal use**. Development priorities and roadmap decisions are driven by our operational needs.

What this means for contributors:

| Contribution type | Status |
|---|---|
| Bug reports | ✅ Welcome |
| Security vulnerability reports | ✅ Please report promptly (see below) |
| Bug fix PRs | ✅ Considered — see guidance below |
| Documentation improvements | ✅ Welcome |
| Feature requests | ⚠️ Unlikely to be prioritised unless aligned with our roadmap |
| Feature PRs | ⚠️ Please discuss before investing effort — we may not accept them |

We will always acknowledge issues and PRs, but **we cannot guarantee that contributions outside of bug fixes will be merged or actioned**.

---

## Reporting Bugs

If you believe you have found a bug, please open a GitHub Issue and include:

- A clear, descriptive title
- Steps to reproduce the issue (screenshots may be useful here)
- Expected vs actual behaviour
- Your Arches version, Python version, and any other relevant environment details
- Any relevant logs or error messages

Please search existing issues before opening a new one.

---

## Security Vulnerabilities

**Please do not report security vulnerabilities via public GitHub Issues.**

Contact customers@historicengland.org.uk and mark the email for the attention of the IMT Arches Development Team so we can assess and address the issue before any public disclosure.

---

## Feature Requests

You are welcome to open an issue to suggest a feature, but please understand that:

- Features are prioritised against Historic England's internal roadmap.
- We may close feature requests that are out of scope without implementing them.  We will try to explain our reasoning when we do so.

If you need a feature for your own Arches deployment, forking this repository and adapting it for your needs may be the most practical route.

---

## Pull Requests

We will consider pull requests that fix confirmed bugs. Before submitting:

1. Check that an issue exists (or open one) describing the bug your pull request addresses
2. Fork the repository and create a branch from the default branch:
   ```bash
   git checkout -b [issue_number]_your_descriptive_branch_name
   ```
3. Keep the change focused — one bug fix per PR
4. Ensure existing tests still pass
5. Describe clearly in the PR what the bug was and how your change fixes it

**We are unlikely to accept pull requests that:**
- Add new features without prior discussion and agreement
- Make significant refactoring changes
- Alter behaviour in ways that could affect Historic England's production systems

We reserve the right to decline any pull request without detailed explanation, though we will aim to give feedback where possible.

---

## Development Setup

Please refer to the [README](./README.md) for setup instructions.

---

## Code Style

- **Python:** Follow [PEP 8](https://pep8.org/).  Must be formatted using black formatter.
- **JavaScript / TypeScript:** Follow existing conventions in the codebase.
- **HTML / Django templates:** Keep templates clean and consistent with existing patterns.

Avoid introducing new dependencies without prior discussion.

---

Thank you for your understanding. If you have questions about whether a contribution would be welcome, feel free to open a discussion issue before investing significant time.