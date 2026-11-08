# Repository Design: Empirical Software Engineering Literature & Architectural Decisions

**Review & Implementation Date:** 8 November 2026  
**Scope:** Information architecture, visual communication, setup cognitive load, feature-to-code traceability, and repository governance in open-source systems software.  
**Boundary:** This document records the conceptual framework behind the repository presentation; it does not substitute for physical hardware acceptance or cryptographic verification.

---

## 1. Empirical Literature & Conceptual Framework

Empirical software engineering studies on open-source adoption, developer trust, and onboarding show that **structured evidence and low cognitive load** outperform decorative elements:

### 1.1 Information Categories and the "Why / Status" Gap
- **Prana, Treude, Thung, Atapattu & Lo (2019)** — *Categorizing the Content of GitHub README Files* (*Empirical Software Engineering*, 24(3), 1296–1327; [arXiv:1802.06997v2](https://arxiv.org/html/1802.06997v2)): Analyzing 4,226 sections across 393 repositories, this study found that while **What** and **How** are ubiquitous, **Why** (architectural rationale over alternatives), **Status** (verified scope vs. unverified boundaries), and **Contribution** are frequently missing.
- **Application in this Repository:** The opening section contrasts Keenetic Hue (`zero inbound ports + outbound Cloudflare Tunnel + isolated service principals + Ed25519-signed releases`) against standard bridge or DIY server setups, and explicitly separates the verified hardware profile from out-of-scope features.

### 1.2 Visual Elements, Code Blocks, and Structural Organization
- **Venigalla & Chimalakonda (2022)** — *What Makes a Good GitHub Project README? An Empirical Study on Correlation Between README Content and Project Popularity* ([arXiv:2206.10772v1](https://arxiv.org/html/2206.10772v1)): Across 1,950 repositories in 10 languages, **structured lists**, **navigational links**, **static architectural diagrams**, **inline/block code examples**, and **references/contribution sections** correlate positively with repository popularity, whereas purely decorative GIFs/videos show negligible impact in systems projects.
- **Wang, Wang, Liu, Zhou & Jin (2023)** — *How README Files Affect the Popularity of GitHub Repositories* (*Journal of Systems and Software*, 204, 111806; [DOI:10.1016/j.jss.2023.111806](https://www.sciencedirect.com/science/article/abs/pii/S0164121223002017)): Across 5,000 repositories, balanced tables, readable hierarchy, and concrete verification outputs significantly improve developer trust.
- **Application in this Repository:** Instead of mock UI screenshots or vanity badges, the repository uses original technical diagrams ([`docs/assets/architecture.svg`](assets/architecture.svg) and [`docs/assets/hero.png`](assets/hero.png)) illustrating trust boundaries, UID/GID separation (`hue-agent:61001`, `hue-tunnel:61000`), loopback/Unix socket endpoints, and a real sanitized `huectl health --live --json` payload.

### 1.3 Managing Cognitive Load in Setup Instructions
- **Gao, Treude & Zahedi (2025)** — *Evaluating Setup and Installation Instructions in GitHub READMEs* ([arXiv:2312.03250v3](https://arxiv.org/html/2312.03250v3)): Studying 1,163 setup commits across 400 Python and Java repositories, the authors show that dumping full operational runbooks into the main README causes **information overload**. The most effective pattern summarizes **(1) Pre-installation**, **(2) Installation**, and **(3) Post-installation Verification** in the README while delegating step-by-step runbooks to dedicated guides.
- **Diátaxis Framework** ([diataxis.fr](https://diataxis.fr/)): Separates installation ([`docs/installation.md`](installation.md)), disaster recovery ([`docs/reinstall.md`](reinstall.md)), daily maintenance quickstart ([`docs/maintenance-quickstart.md`](maintenance-quickstart.md)), troubleshooting ([`docs/troubleshooting.md`](troubleshooting.md)), and architecture explanation ([`docs/architecture.md`](architecture.md)).

### 1.4 Feature-to-Code Traceability
- **Puhlfürß, Montgomery & Maalej (2022/2024)** — *An Exploratory Study of Feature Visuals and Code Links in README Files* ([arXiv:2208.01317v1](https://arxiv.org/html/2208.01317v1)): Analyzing 480 feature descriptions across 25 popular repositories ($\ge 5,000$ stars), the study found that **32% of features lacked any usage or code link**. Linking high-level capabilities directly to implementation modules and test files bridges user comprehension and code auditing.
- **Application in this Repository:** The main README includes a direct traceability matrix mapping every security and resilience mechanism to `hue_agent/`, `hue_admin/`, `cloud/src/`, and `tests/`.

### 1.5 Standard Repository Anatomy and Governance Signals
- **Hora, Montandon & Costa (2026)** — *Anatomy of GitHub Repositories* ([arXiv:2605.16701v2](https://arxiv.org/html/2605.16701v2)) and **Gaughan, Champion & Hwang (2025)** — *Governance and Maintenance Signals in FLOSS Repositories* ([arXiv:2502.18440v2](https://arxiv.org/html/2502.18440v2)): Across 10,000 active GitHub repositories and 4,226 upstream FLOSS packages, predictable root governance files ([`README.md`](../README.md), [`LICENSE`](../LICENSE), [`SECURITY.md`](../SECURITY.md), [`CONTRIBUTING.md`](../CONTRIBUTING.md), [`CODE_OF_CONDUCT.md`](../CODE_OF_CONDUCT.md), [`SUPPORT.md`](../SUPPORT.md), `.github/workflows`, `.github/ISSUE_TEMPLATE`) paired with verifiable CI checks serve as primary indicators of engineering maturity.

---

## 2. Domain Precedents

- **[Google Bumble](https://github.com/google/bumble):** Informed how user-space USB HCI transport requirements, pinned dependencies, and hardware scope boundaries are presented. Bumble's broader Bluetooth protocol support is not claimed as part of this project.
- **[bleak-esphome](https://github.com/Bluetooth-Devices/bleak-esphome):** Informed the clear separation between the local BLE radio bridge and upstream automation layers.

---

## 3. Applied Design Decisions

| Design Dimension | Literature Basis | Applied Decision |
| --- | --- | --- |
| **Positioning (`Why` & `Status`)** | Prana et al. (2019) | Side-by-side architectural trade-off table and explicit RC12 acceptance boundaries at the top of the README. |
| **Technical Visuals (`hero.png`, `architecture.svg`)** | Venigalla & Chimalakonda (2022); GitHub Social Preview (1280×640, <1 MB) | Original vector/PNG diagrams with zero third-party vendor logos, showing trust zones (`Cloudflare` ↔ `Keenetic Entware` ↔ `Hue BLE`), UID/GID isolation, and local sockets. |
| **3-Stage Setup & Verification** | Gao et al. (2025); Diátaxis | Pre-installation → Signed Installation → `huectl health --live --json` verification in the README, backed by 8 task-oriented guides. |
| **Feature → Module → Test Matrix** | Puhlfürß et al. (2022/2024) | Direct clickable links from every architectural claim to its Python/TypeScript source module and unit test suite. |
| **Publication Hygiene & Evidence Separation** | Hora et al. (2026); [`tools/check_publication.py`](../tools/check_publication.py) | Automated offline check rejects private keys, BLE bonds, tokens, `.huebak` archives, and personal paths; portable unit tests (81 Python + 64 Worker) are kept distinct from physical hardware acceptance. |
