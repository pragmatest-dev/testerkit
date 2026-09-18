# Station & Machine Identity: Prior Art Survey

**Purpose:** Inform TesterKit's machine/station identity model by surveying how established
hardware/electronics test solutions have identified test **stations** and **machines** over time.

**Scope:** Focused (not exhaustive). Facts are cited to official docs/primary sources where
possible; forum threads and secondary sources are labeled as such. Where a claim could not be
verified against a primary source it is marked **UNVERIFIED** or **PARTIALLY VERIFIED**.

**Method:** Web research. All URLs fetched/searched on **2026-09-18**.

---

## 1. How established frameworks identify a test station

### 1.1 NI TestStand — `StationOptions.StationID`

- The Station ID "specifies a test station identification string for this instance of the
  TestStand Engine" and "never returns an empty string" (NI TestStand API Reference,
  `StationOptions.StationID`, https://www.ni.com/docs/en-US/bundle/teststand-api-reference/page/tsapiref/stationoptions-stationid.html
  — page title/property confirmed via NI search index 2026-09-18; the property page body did
  not render through the fetcher, so wording is quoted from NI's search-result excerpt).
- It is a **config-stored string**, not a live hardware read. All TestStand station
  configuration lives in **`TestExec.ini`** in the `<TestStand>/cfg` folder (NI Community,
  "Where is the station options configuration stored",
  https://forums.ni.com/t5/NI-TestStand/Where-is-the-station-options-configuration-stored/td-p/1391646,
  2026-09-18). Settings are edited via **Configure » Station Options** and can also be set
  programmatically through the `StationOptions` object; they "affect all sequence editor and
  operator interface sessions that you run on your computer" (NI Community, "Changing station
  options programmatically",
  https://forums.ni.com/t5/NI-TestStand/Changing-station-options-programmatically/td-p/272893,
  2026-09-18).
- There is an explicit **"use computer name as station id"** preference — i.e. TestStand lets
  you choose between an assigned/config string and the hostname (surfaced in NI search index
  for the Station Options preferences, 2026-09-18). This confirms both modes coexist and the
  choice is config-controlled.
- At runtime the value is exposed to sequences via `RunState.Root.Locals.StationInfo.StationID`;
  it is only populated during execution (NI Community, "StationID. How to get it from within LV
  module", https://forums.ni.com/t5/NI-TestStand/StationID-How-to-get-it-from-within-LV-module/td-p/2731103,
  and "How To change Station Id Value in TestStand",
  https://forums.ni.com/t5/NI-TestStand/How-To-change-Station-Id-Value-in-Teststand/td-p/4422893,
  2026-09-18).
- It flows into database logging as the `STATION_ID` column of the result schema (NI TestStand
  database-logging schema; the schema's own primary keys can be Number/String/**GUID** per the
  selected schema — NI search index for UUT_RESULT [ID], 2026-09-18). Note: the GUID is the
  *result-record* key, not the Station ID itself.

**Default value:** It is widely reported that the default `StationID` is a **GUID generated on
first install** (so two fresh installs differ), editable afterward. This is consistent with the
"never empty" guarantee and the separate "use computer name" toggle, but I could **not** confirm
the GUID default against a primary NI page (the API page body did not render). **PARTIALLY
VERIFIED** — treat "GUID default, config-editable string, stored in `TestExec.ini`" as
high-confidence on the config/editable/storage parts, lower-confidence on the exact GUID default.

**Verdict:** TestStand Station ID is **config-assigned and editable** (persisted in a config
file), with an optional hostname mode. Not hardware-derived, not server-issued.

### 1.2 OpenHTF (Google) — `station_id`

- `station_id` is **config-declared with the hostname as default**. From source
  (`openhtf/core/test_state.py`, fetched 2026-09-18):
  ```python
  CONF.declare(
      'station_id',
      'The name of this test station',
      default_value=socket.gethostname())
  ```
  (https://github.com/google/openhtf/blob/master/openhtf/core/test_state.py)
- It is "the name of the test station written to every record," and can be overridden via a
  config file, `conf.load(station_id=...)`, or `conf.load_from_dict(...)`; absent any of those it
  falls back to `socket.gethostname()` (OpenHTF configuration docs / spintop-openhtf,
  https://www.openhtf.com/configuration and
  https://spintop-openhtf.readthedocs.io/en/latest/docs/config/teststation.html — "The id used is
  the hostname of the PC on which the test bench runs," retrieved via `conf.station_id`,
  2026-09-18).

**Verdict:** OpenHTF is **hostname-by-default, config-overridable**. Identity is a plain string,
no GUID, no central registry in the core framework.

### 1.3 NI SystemLink / MAX — central registration of "systems"

- When a client system connects to a SystemLink Server, "the server uses the system's
  **hostname** to identify it," which is "often not human readable." Operators set a
  human-friendly **alias** on the System Settings tab "to make the system easy to identify" (NI
  SystemLink docs, "Modifying the Settings of a System",
  https://www.ni.com/docs/en-US/bundle/systemlink/page/modying-setting-of-connected-system.html,
  via NI search excerpt 2026-09-18 — the doc body is behind NI's JS shell but the excerpt is from
  the same page).
- So SystemLink layers a **server-side registry** on top of the machine's hostname: the transport
  identity is the hostname, and the human-facing identity is an editable alias managed centrally.

**Verdict:** SystemLink = **hostname as the connect-time key + server-registered editable alias**
as the human identity. Central registration, but the underlying machine key is still the hostname.

### 1.4 Semiconductor ATE + MES (SECS/GEM)

- SECS/GEM is the equipment-to-host interface between process/test equipment and the fab host /
  MES (Wikipedia "SECS/GEM", https://en.wikipedia.org/wiki/SECS/GEM; SEMI, "Intro to SECS/GEM",
  https://www.semi.org/en/standards-watch-2022-Sept/intro-to-semi-communication-standards,
  2026-09-18).
- At the transport/session layer, HSMS/SECS-I uses a **Device ID** to address the equipment on the
  link (secsgem docs, https://secsgem.readthedocs.io/en/latest/firststeps/gemequipment.html,
  2026-09-18).
- At the application layer, the host sends **S1F1 (Are You There)** and the equipment replies
  **S1F2 (On-Line Data)** carrying **MDLN** (equipment model name) and **SOFTREV** (software
  revision) as ASCII strings (secsgem/Ignition SECS-GEM docs and einnosys data-items guide,
  https://www.einnosys.com/secs-gem-data-items/,
  https://www.docs.inductiveautomation.com/docs/8.1/ignition-modules/secs-gem, 2026-09-18).
- Crucially, **MDLN/SOFTREV describe the *model and software*, not a unique per-unit identity.**
  The unique "which physical tester is this" mapping is **assigned by the fab/MES** — the MES
  keys equipment by an **EquipmentID / tool name** it configures and associates with the SECS
  connection (Device ID + host-side config). Data items themselves are addressed by SVID/DVID/ECID
  namespaces defined per tool (einnosys, https://www.einnosys.com/secs-gem-data-items/,
  2026-09-18).

**Verdict:** In the ATE/MES world, the **physical tester's unique identity is MES-assigned**
(equipment id / tool name held by the host), while the equipment self-reports only model +
software revision. Logical identity (MES) is deliberately **separated** from what the box reports
about itself.

---

## 2. Instrument / asset identity (serials aren't globally unique)

- NI SystemLink **Asset Manager** auto-detects SCPI instruments via the `*IDN?` response in the
  form `<manufacturer>,<model>,<serial number>,<firmware>` — i.e. identity is the **tuple**, not
  the serial alone. Manufacturer + model + serial together disambiguate same-serial-across-vendors
  (NI Community, "Adding Assets to SystemLink",
  https://forums.ni.com/t5/SystemLink/Adding-Assets-to-SystemLink/ta-p/4056570, 2026-09-18).
- The SystemLink **Asset Module** "records, tracks, controls, and reports on test assets from
  procurement to disposal," centralizing "hardware details, serial numbers, and calibration
  records," and monitors "calibration state, history, and forecast of all connected assets" (NI,
  "What is SystemLink Asset Module?",
  https://www.ni.com/en/shop/electronic-test-instrumentation/add-ons-for-electronic-test-and-instrumentation/what-is-systemlink-asset-module.html,
  2026-09-18).
- Assets NI-VISA can't auto-discover (some GPIB/USB-TMC/LXI, or non-instrument gear) can be added
  and enriched (e.g. calibration history) via the **Asset Management API** (NI Community, same
  thread; nisystemlink Python client,
  https://python-docs.systemlink.io/en/stable/api_reference/assetmanagement.html, 2026-09-18).

**Verdict on disambiguation:** The accepted key is **(vendor, model, serial)** — the `*IDN?`
tuple — plus a **central asset record** (calibration/traceability) rather than trusting the serial
to be globally unique. Metrology traceability rides on that asset record, not on the station id.

---

## 3. Re-imaged / cloned / swapped station controller

TestStand and OpenHTF station ids are **config/hostname values**, so their survival depends
entirely on whether the config (or hostname) is carried in the image:

- **TestStand:** identity lives in `TestExec.ini` (§1.1). If that config is part of a cloned
  image, the Station ID is **copied** — every clone reports the same id until changed. NI's own
  deployment tooling deploys station config/preferences, which is the mechanism by which the same
  id propagates (NI, "Deploy Global Configuration Settings with TestStand Deployment Utility",
  https://knowledge.ni.com/KnowledgeArticleDetails?id=kA00Z000000P7ZOSA0; NI Community, "How to
  Deploy Station Preferences",
  https://forums.ni.com/t5/NI-TestStand/How-to-Deploy-Station-Preferences/td-p/247325, 2026-09-18).
  I found **no NI guidance that auto-detects duplicate Station IDs after cloning** — avoiding
  collisions is left to the deployer (**UNVERIFIED that any collision guard exists**).
- **OpenHTF:** if left at default it is the **hostname**, so a cloned image with a stale hostname
  yields duplicate station ids until the hostname (or config override) is changed (§1.2). The core
  framework has no clone/duplicate detection.
- **SystemLink:** connect-time identity is the hostname; a re-imaged PC with the same hostname
  looks like the same system, and the human alias is a server-side record that persists
  independent of the box (§1.3).

**The OS-level analogue is the well-established answer to this exact problem: `systemd`'s
`machine-id`.**

- machine-id is "a single newline-terminated, hexadecimal, 32-character, lowercase ID" that
  "**should be considered confidential** and must not be exposed in untrusted environments";
  it "does not change based on... hardware... or randomly" once set (machine-id(5),
  https://man7.org/linux/man-pages/man5/machine-id.5.html;
  https://www.freedesktop.org/software/systemd/man/latest/machine-id.html, 2026-09-18).
- For images used on many machines, the authoritative guidance is to **generate on first boot,
  not bake in**: "For operating system images which are created once and used on multiple
  machines... `/etc/machine-id` should be either missing or an empty file... an ID will be
  generated during boot and saved." And explicitly: "Remove the `/etc/machine-id` file or write
  the string `uninitialized\n` into it. Only when it is reset will it be auto-generated on first
  boot and thus be truly unique. If this file is not reset... every instance of the system will
  come up with the same ID and that will likely lead to problems sooner or later" (systemd,
  "Safely Building Images", https://systemd.io/BUILDING_IMAGES/, 2026-09-18).
- The same image-prep guidance says to also reset **other seeded identity/secret resources**:
  `/var/lib/systemd/random-seed`, the boot-loader `/loader/random-seed`, `/etc/hostname` /
  `/etc/machine-info`, and `/var/lib/systemd/credential.secret` (systemd, "Safely Building
  Images", 2026-09-18).
- `systemd-firstboot --reset` and `systemd-machine-id-setup` are the tooling that implements
  generate-on-first-boot; writing `uninitialized` marks the next boot as a first boot
  (systemd-firstboot / systemd-machine-id-setup manpages,
  https://www.freedesktop.org/software/systemd/man/latest/systemd-machine-id-setup.html;
  https://manpages.ubuntu.com/manpages/focal/man1/systemd-machine-id-setup.1.html, 2026-09-18).

**Verdict:** Test frameworks themselves offer little clone protection — their station ids are
config/hostname and simply copy. The mature, cross-industry pattern for a *machine* identity that
survives use but not cloning is systemd's: **generate-on-first-run, blank it in image prep, never
bake a fixed id into the golden image.**

---

## 4. Trend over time

Synthesizing the above:

1. **Away from raw hostname as the identity, toward an assigned/registered string.** OpenHTF
   defaults to hostname but exists to be overridden; SystemLink keeps hostname only as the
   connect key and puts a human-managed **alias** on top; TestStand stores an editable id in
   config with an optional hostname mode. The gravitational pull is toward a **stable assigned
   id decoupled from the volatile hostname.**
2. **Toward server/central registration for the human-facing identity** (SystemLink alias; MES
   equipment id). The authoritative "which station is this" increasingly lives in a **registry**,
   not on the box.
3. **Explicit separation of logical station identity from physical asset identity.** ATE/MES is
   the clearest: MES-assigned equipment id (logical) vs equipment-reported MDLN/SOFTREV, and a
   separate **asset registry** keyed by `(vendor, model, serial)` for the instruments. SystemLink
   mirrors this: a *system* (station) is distinct from the *assets* (instruments) it contains.
4. **Hardware fingerprints are used for asset identity, not station identity.** The `*IDN?` tuple
   and calibration records identify *instruments*; nobody surveyed derives the *station* identity
   from a hardware fingerprint. At the machine/OS layer, the durable machine identifier
   (systemd machine-id) is a **generated GUID-like value**, explicitly *not* a hardware read.

---

## TesterKit implications (RECOMMENDATION vs FINDING)

**FINDING — the prior art supports TesterKit's proposed layering.** The four-layer split TesterKit
proposes maps cleanly onto what the T&M/ATE world already does:

| TesterKit layer | Closest prior art | Nature in prior art |
|---|---|---|
| **station = config-assigned canonical id** | TestStand `StationID` (config, editable); SystemLink alias; MES equipment id | assigned/registered string, not hardware-derived |
| **machine/controller = auto support signal (global GUID, not canonical)** | systemd `machine-id` | generated GUID-like value, generate-on-first-boot, confidential |
| **instruments/assets = serial + best-available identity** | NI Asset Manager `*IDN?` `(vendor,model,serial)` + central asset/cal record | tuple key, never serial alone |
| **software = per-run env fingerprint** | OpenHTF SOFTREV-style / SystemLink software inventory | per-record captured, not identity |

The one nuance: keep the machine GUID **as a support/telemetry signal, not the canonical id** —
prior art shows the canonical station identity is the *assigned* one (TestStand config string,
SystemLink alias, MES equipment id), while the machine-derived value is a secondary aid. That is
exactly TesterKit's stated split. **(FINDING: the gravitational pull matches the proposed model.)**

**FINDING — deployed/cloned-image answer.** Test frameworks themselves do **not** solve clone
collisions (TestStand config and OpenHTF hostname both simply copy into clones; no auto
duplicate-detection was found — the collision-avoidance is left to the deployer). The mature,
cross-industry answer comes from the OS layer: **generate-on-first-run and blank the id during
image prep** so the golden image contains no id (systemd machine-id: remove/`uninitialized`, then
auto-generate on first boot; systemd, "Safely Building Images", 2026-09-18). "Bake a fixed id into
the image" is explicitly the anti-pattern systemd warns against.

**RECOMMENDATION for TesterKit (clearly a recommendation, not a finding):**
- Treat the **machine/controller GUID like `machine-id`**: generate-on-first-run, store it in a
  file that image-prep is expected to blank (document a `testerkit`-analogue of
  `systemd-firstboot --reset`). Do **not** bake it into a golden image.
- Do **not** rely on the machine GUID as the canonical station id — require an **assigned station
  id** (config/registry) as canonical, matching TestStand/SystemLink/MES.
- Add **server-side duplicate detection** as a safety net (two live stations reporting the same
  canonical id, or the same machine GUID appearing on two hostnames = probable clone). Prior art
  does *not* provide this in the frameworks, so it's a genuine value-add rather than a
  reinvention — but position it as a guard, not the identity mechanism.
- Key instruments by the **`(vendor, model, serial)` tuple** plus a central asset/cal record, not
  by serial alone — this is the settled T&M practice.

---

## Source list (all accessed 2026-09-18)

- NI TestStand API Reference, `StationOptions.StationID`:
  https://www.ni.com/docs/en-US/bundle/teststand-api-reference/page/tsapiref/stationoptions-stationid.html
- NI Community, station options storage (`TestExec.ini`):
  https://forums.ni.com/t5/NI-TestStand/Where-is-the-station-options-configuration-stored/td-p/1391646
- NI Community, changing station options programmatically:
  https://forums.ni.com/t5/NI-TestStand/Changing-station-options-programmatically/td-p/272893
- NI Community, StationID from a module / runtime access:
  https://forums.ni.com/t5/NI-TestStand/StationID-How-to-get-it-from-within-LV-module/td-p/2731103
- NI Community, changing Station ID value:
  https://forums.ni.com/t5/NI-TestStand/How-To-change-Station-Id-Value-in-Teststand/td-p/4422893
- NI, Deploy Global Configuration Settings with TestStand Deployment Utility:
  https://knowledge.ni.com/KnowledgeArticleDetails?id=kA00Z000000P7ZOSA0
- NI Community, How to Deploy Station Preferences:
  https://forums.ni.com/t5/NI-TestStand/How-to-Deploy-Station-Preferences/td-p/247325
- OpenHTF source, `station_id` declaration:
  https://github.com/google/openhtf/blob/master/openhtf/core/test_state.py
- OpenHTF configuration docs: https://www.openhtf.com/configuration
- spintop-openhtf, Test Station Configuration:
  https://spintop-openhtf.readthedocs.io/en/latest/docs/config/teststation.html
- NI SystemLink, Modifying the Settings of a System (hostname + alias):
  https://www.ni.com/docs/en-US/bundle/systemlink/page/modying-setting-of-connected-system.html
- NI, What is SystemLink Asset Module:
  https://www.ni.com/en/shop/electronic-test-instrumentation/add-ons-for-electronic-test-and-instrumentation/what-is-systemlink-asset-module.html
- NI Community, Adding Assets to SystemLink (`*IDN?` tuple):
  https://forums.ni.com/t5/SystemLink/Adding-Assets-to-SystemLink/ta-p/4056570
- nisystemlink Asset Management client:
  https://python-docs.systemlink.io/en/stable/api_reference/assetmanagement.html
- SEMI, Intro to SECS/GEM:
  https://www.semi.org/en/standards-watch-2022-Sept/intro-to-semi-communication-standards
- Wikipedia, SECS/GEM: https://en.wikipedia.org/wiki/SECS/GEM
- secsgem docs, GEM equipment (Device ID):
  https://secsgem.readthedocs.io/en/latest/firststeps/gemequipment.html
- einnosys, SECS/GEM Data Items (MDLN/SOFTREV, SVID/DVID/ECID):
  https://www.einnosys.com/secs-gem-data-items/
- Ignition SECS/GEM module docs:
  https://www.docs.inductiveautomation.com/docs/8.1/ignition-modules/secs-gem
- systemd, machine-id(5): https://man7.org/linux/man-pages/man5/machine-id.5.html and
  https://www.freedesktop.org/software/systemd/man/latest/machine-id.html
- systemd, Safely Building Images: https://systemd.io/BUILDING_IMAGES/
- systemd-machine-id-setup manpage:
  https://www.freedesktop.org/software/systemd/man/latest/systemd-machine-id-setup.html
- systemd-machine-id-setup (Ubuntu manpage):
  https://manpages.ubuntu.com/manpages/focal/man1/systemd-machine-id-setup.1.html
