<!--
  AUTHORITATIVE SPEC — DO NOT HAND-EDIT.
  Source: usdm4_excel 0.10.0 wheel, dist-info/METADATA (long_description).
  usdm4_excel has no public GitHub repo; this README ships only inside the package.
  Retrieved: 2026-09-12. Targets USDM v4.
  Machine truth for column layouts is the package source, not this prose (see AGENTS/README).
-->

# USDM4_Excel Package
Library for import and export of USDM Version 4 via MS Excel

## Implementation Status

This README documents the target sheet formats. Not every documented sheet is implemented yet — the table below is the source of truth (last audited 2026-07-30, see `docs/completion_plan.md` for the completion plan).

| Sheet | Import | Export |
|-------|--------|--------|
| configuration | yes | yes |
| study, studyIdentifiers, studyOrganizations | yes | yes |
| studyReferences (reference identifiers) | yes | yes |
| dates (separate sheet + legacy table on study sheet) | yes | yes (both) |
| roles, extensions | yes | yes |
| documents, documentVersions, document content/templates | yes | yes |
| studyAmendments, amendmentChanges, amendmentImpact | yes | yes |
| abbreviations | yes | yes |
| notes | yes | yes |
| dictionaries | yes | yes |
| people (assigned persons) | yes | yes |
| studyDesignSites | yes | yes |
| studyDesign, arms, epochs, elements, encounters, timing, activities | yes | yes |
| SoA / timelines | yes (multiple per design) | yes |
| studyDesignPopulations | yes | yes |
| studyDesignOE (objectives & endpoints) | yes | yes |
| eligibility criteria (legacy one-sheet) | yes (fallback) | yes |
| eligibilityCriteria + eligibilityCriteriaItems (split) | yes (preferred) | yes (both dialects) |
| studyDesignConditions, interventions | yes | yes |
| studyDesignProcedures (definitions, referenced from SoA rows) | yes | yes |
| studyDesignEstimands | yes | yes |
| studyDesignIndications | yes | yes |
| studyDesignCharacteristics (cohort characteristic definitions) | yes | yes |
| studyDesignSpecimen (definitions, referenced from studyDesign sheet) | yes | yes |
| studyProducts, studyProductOrganizationRoles | yes | yes |
| studyDevices | yes | yes |

A JSON → Excel → JSON round-trip test (`tests/usdm4_excel/test_round_trip.py`) guards the import/export pairing. No known import/export asymmetries remain. Note: `CommentAnnotation` stores no name, so exported notes carry synthesised names (`NOTE_1`, `NOTE_2`, ...) on the notes sheet, referenced from each sheet's `notes` column — the original workbook's note names are import keys only and are not preserved.

## Workbook Formats

The package reads and writes two formats:

**Legacy single workbook.** Every sheet in one file, compatible with workbooks written for the old `usdm` / `usdm4_legacy_excel` packages. Unmodified legacy workbooks import directly: the study sheet's dates table, `protocolVersion`/`protocolStatus` keys and configuration `TEMPLATE` mappings (from which the study definition documents are synthesised), legacy column names (`siteName`, `studyEpochType`, `encounterType`, …) and `xref` reference columns are all understood. Export in this format (`to_excel(..., format="legacy")` or `to_legacy_excel`) writes both dialects where they differ (legacy dates table + `dates` sheet; legacy one-sheet eligibility + split sheets) so the file is readable by both old and new importers.

**Multi-workbook.** One main workbook plus one workbook per study design, for complex multi-design studies (`to_excel(..., format="multi")`):

- The main workbook holds the study/version-level sheets: study, dates, identifiers, organizations, roles, extensions, people, sites, documents and content, amendments, abbreviations, dictionaries, eligibilityCriteriaItems, conditions, interventions, products, devices, product organization roles, configuration.
- The study sheet's `studyDesigns` row lists the design workbook file names, comma-separated, relative to the main workbook (export names them `<main-stem>_<design-name>.xlsx`).
- Each design workbook holds that design's sheets: studyDesign, arms, epochs, elements, encounters, activities, timing, populations, objectives & endpoints, eligibilityCriteria, indications, estimands, procedures, characteristics, specimen retentions, and the timeline (SoA) sheets.
- The main workbook must NOT also contain an embedded `studyDesign` sheet when `studyDesigns` lists files — the import rejects this as ambiguous.
- On import the format is auto-detected from the `studyDesigns` row; no flag is needed.

**Name uniqueness rule.** Cross-references are resolved by name in a single study-wide namespace: populations, cohorts, arms, epochs, elements, encounters, activities, timings, procedures, characteristics, criterion items, etc. must have names that are unique across the WHOLE study, including across design workbooks. Two designs may not both define, say, a population named `POP1` — the second is dropped with an error.

## Table of Contents

- [Export](#Export)
- [Import](#Import)
  - [General Sheets](#general-sheets)
    - [Configuration Sheet](#configuration-sheet)
    - [Dates Sheet](#dates-sheet)
    - [Dictionaries Sheet](#dictionaries-sheet)
    - [Notes Sheet](#notes-sheet)
  - [Study Sheets](#study-sheets)
    - [Study Sheet](#study-sheet)
    - [Study Organizations Sheet](#study-organizations-sheet)
    - [Study Identifiers Sheet](#study-identifiers-sheet)
    - [Study References Sheet](#study-references-sheet)
    - [Documents Sheet](#documents-sheet)
    - [Document Versions Sheet](#document-versions-sheet)
    - [Abbreviation Sheet](#abbreviation-sheet)
    - [Amendment Sheet](#amendment-sheet)
    - [Amendment Changes Sheet](#amendment-changes-sheet)
    - [Amendment Impact Sheet](#amendment-impact-sheet)
    - [Assigned Person Sheet](#assigned-person-sheet)
    - [Roles Sheet](#roles-sheet)
    - [Document Content Sheet](#document-content-sheet)
    - [Document Template Sheets](#document-template-sheets)
    - [Eligibility Criteria Items Sheet](#eligibility-criteria-items-sheet)
    - [Extensions Sheet](#extensions-sheet)
    - [Sites Sheet](#sites-sheet)
  - [Study Design Sheets](#study-design-sheets)
    - [Study Design Sheet](#study-design-sheet)
    - [Study Design Arms Sheet](#study-design-arms-sheet)
    - [Study Design Characteristics Sheet](#study-design-characteristics-sheet)
    - [Study Design Encounters Sheet](#study-design-encounters-sheet)
    - [Study Design Epochs Sheet](#study-design-epochs-sheet)
    - [Study Design Elements Sheet](#study-design-elements-sheet)
    - [Study Design Activities Sheet](#study-design-activities-sheet)
    - [Study Design Timing Sheet](#study-design-timing-sheet)
    - [Timeline (SoA) Sheets](#timeline-soa-sheets)
    - [Study Design Populations Sheet](#study-design-populations-sheet)
    - [Study Design Objectives and Endpoints Sheet](#study-design-objectives-and-endpoints-sheet)
    - [Eligibility Criteria Sheet](#eligibility-criteria-sheet)
    - [Study Design Eligibility Criteria Sheet (Legacy)](#study-design-eligibility-criteria-sheet-legacy)
    - [Study Design Conditions Sheet](#study-design-conditions-sheet)
    - [Study Design Interventions Sheet](#study-design-interventions-sheet)
    - [Study Design Procedures Sheet](#study-design-procedures-sheet)
    - [Study Design Estimands Sheet](#study-design-estimands-sheet)
    - [Study Design Indications Sheet](#study-design-indications-sheet)
    - [Study Design Specimen Sheet](#study-design-specimen-sheet)
    - [Study Products Sheet](#study-products-sheet)
    - [Study Product Organization Roles Sheet](#study-product-organization-roles-sheet)
    - [Study Devices Sheet](#study-devices-sheet)
  - [Data Format Guidelines](#data-format-guidelines)
  - [CDISC CT Code Lists](#cdisc-ct-code-lists)

# Export

A simple export mechanism is in place to export a USDM file to the v4 CDISC Excel format

# Import

## General

This section describes all the columns used within the Excel sheets for USDM4 data import/export, along with the expected formats and values for each column. This format is an updated version of the CDISC v4 Excel format that allows for multiple study designs (one workbook per design) and a few other improvements. See the Implementation Status table above for what is currently wired in.

## General Sheets

### Configuration Sheet

**Sheet Name:** `configuration`

This sheet configures CDISC CT (Controlled Terminology) versions for the import process.

| Column | Format | Description | Example |
|--------|--------|-------------|---------|
| **Name** (Column A) | Text | Configuration parameter name (case-insensitive) | `CT VERSION` |
| **Value** (Column B) | Text | Configuration parameter value | `SDTMCT = 2025-03-28` |

**Supported Configuration Parameters:**
- `CT VERSION`: Specifies CDISC CT version in format `<CT name> = <version>`
  - Valid CT names: `SDTMCT`, `PROTOCOLCT`, `DDFCT`
  - Version format: `YYYY-MM-DD`
- `TEMPLATE` (legacy): maps a document template to the sheet holding its
  narrative content, in format `<template name> = <sheet name>`. The default
  mapping `SPONSOR = document` always applies.
- The legacy options `EMPTY NONE`, `USE TEMPLATE`, `USDM VERSION`,
  `SDR PREV NEXT`, `SDR ROOT` and `SDR DESCRIPTION` are accepted with a
  deprecation warning and their values ignored.

**Examples:**
```
CT VERSION | SDTMCT = 2025-03-28
CT VERSION | PROTOCOLCT = 2025-03-28
CT VERSION | DDFCT = 2025-03-28
TEMPLATE   | M11 = m11Template
```

### Dates Sheet

**Sheet Name:** `dates`

Defines governance dates for the study version, amendments and document versions. This is the preferred dialect; the legacy dates table on the study sheet is still read when this sheet is absent.

| Column | Format | Description | Required | CDISC CT Code List | Example |
|--------|--------|-------------|----------|-------------------|---------|
| **name** | Text | Date name (reference key) | Yes | - | `Approval Date 1` |
| **description** | Text | Date description | Yes | - | `Date of protocol approval` |
| **label** | Text | Date label | Yes | - | `Approval` |
| **type** | CDISC CT | Type of governance date | Yes | C207413 | `Approval Date` |
| **dateValue** | Date | Date value | Yes | - | `2024-01-15` |
| **geographicScopes** | Geographic Scopes | Comma-separated scopes: `Global`, `Region: <name>`, `Country: <name or code>` | No (defaults to `Global`) | C207412 | `Global` |
| **category** | Text | `study_version` (default), `protocol_document` or `amendment`. Only `study_version` dates attach to the study version; the others are referenced by name from the documentVersions / studyAmendments sheets | No | - | `study_version` |

### Dictionaries Sheet

**Sheet Name:** `dictionaries`

This sheet defines syntax template dictionaries for parameterized text.

| Column | Format | Description | Required | Example |
|--------|--------|-------------|----------|---------|
| **name** | Text | Dictionary name | Yes | `Treatment Duration` |
| **description** | Text | Dictionary description | Yes | `Template for treatment duration text` |
| **label** | Text | Dictionary label | Yes | `Duration Template` |
| **key** | Text | Parameter key/tag | Yes | `duration` |
| **class** | Text | API class name for cross-reference | No | `Duration` |
| **reference** / **xref** | Text | Referenced object's name (`xref` is the legacy heading) | No | `Treatment Duration` |
| **attribute** / **path** | Text | Attribute path for cross-reference | No | `quantity.value` |
| **value** | Text | Static value (when not using cross-reference) | No | `12 weeks` |

**Notes:**
- Multiple parameter maps can be defined for the same dictionary
- Either use `class`/`reference`/`attribute` for dynamic references or `value` for static text
- Cross-references link to other API objects created in the system

### Notes Sheet

**Sheet Name:** `notes`

This sheet defines comment annotations that can be referenced by other sheets.

| Column | Format | Description | Required | Example |
|--------|--------|-------------|----------|---------|
| **name** | Text | Note identifier/name | Yes | `Safety Note 1` |
| **text** | Text | Note content/description | Yes | `Monitor for adverse events` |
| **codes** | CDISC CT Codes | Comma-separated list of codes | No | `C49667:Safety Study, C25256:Adverse Event` |

**Code Format:**
- Format: `<code>:<preferred_term>` or `<system>:<code>=<preferred_term>`
- Multiple codes separated by commas
- Example: `C49667:Safety Study, LOINC:LA6115-6=Mild`

## Study Sheets

### Study Sheet

**Sheet Name:** `study`

This sheet defines the main study information using a key-value format (Column A = Parameter Name, Column B = Value).

| Parameter Name | Format | Description | Required | CDISC CT Code List | Example |
|----------------|--------|-------------|----------|-------------------|---------|
| **name** | Text | Study name | Yes | - | `Phase III Efficacy Study` |
| **description** | Text | Study description | Yes | - | `A randomized controlled trial` |
| **label** | Text | Study label | Yes | - | `Study ABC-123` |
| **studyVersion** | Text | Study version identifier | Yes | - | `1.0` |
| **studyAcronym** | Text | Study acronym | No | - | `EFFICACY-III` |
| **studyRationale** | Text | Study rationale | No | - | `To evaluate efficacy and safety` |
| **businessTherapeuticAreas** | CDISC CT Codes | Therapeutic areas (multiple) | No | - | `C3262:Oncology, C3263:Cardiology` |
| **briefTitle** | Text | Brief study title | No | - | `Efficacy Study of Drug X` |
| **officialTitle** | Text | Official study title | No | - | `A Phase III Study of Drug X` |
| **publicTitle** | Text | Public study title | No | - | `Drug X Study for Cancer` |
| **scientificTitle** | Text | Scientific study title | No | - | `Efficacy of Drug X in Advanced Cancer` |
| **studyDesigns** | File References | Study design file names (multiple) | No | - | `design1.xlsx, design2.xlsx` |
| **notes** | Note References | Comma-separated note names | No | - | `Version Note` |
| **protocolVersion** | Text | Protocol version (legacy; drives document synthesis when the documents sheets are absent) | No | - | `2.1` |
| **protocolStatus** | CDISC CT | Protocol status (legacy) | No | C188723 | `Final` |

The legacy keys `studyType`, `studyPhase` (moved to the studyDesign sheet) and `studyTitle` (deprecated) are recognised but ignored with a warning.

**Governance Dates Section (legacy dialect):**
A blank key row ends the key/value block; the row after next starts a dates table with the following fixed column order (only read when the separate `dates` sheet is absent):

| Column | Format | Description | Required | CDISC CT Code List | Example |
|--------|--------|-------------|----------|-------------------|---------|
| **category** | Text | Date category | Yes | - | `study_version`, `protocol_document`, `amendment` |
| **name** | Text | Date name | Yes | - | `Study Start Date` |
| **description** | Text | Date description | Yes | - | `Date when study enrollment begins` |
| **label** | Text | Date label | Yes | - | `Start Date` |
| **type** | CDISC CT | Type of governance date | Yes | C207413 | `Effective Date` |
| **date** | Date | Date value | Yes | - | `2024-01-15` |
| **scopes** | Geographic Scopes | Comma-separated scopes: `Global`, `Region: <name>`, `Country: <name or code>` | No (defaults to `Global`) | - | `Global` |

**Valid protocolStatus values (C188723):**
- `Approved` (C25425)
- `Draft` (C85255)
- `Final` (C25508)
- `Obsolete` (C63553)
- `Pending Review` (C188862)

**Valid governance date type values (C207413):**
- `Approval Date` (C71476)
- `Effective Date` (C215663)
- `Issued Date` (C215664)

### Study Organizations Sheet

**Sheet Name:** `studyOrganizations`

Defines organizations involved in the study.

| Column | Format | Description | Required | CDISC CT Code List | Example |
|--------|--------|-------------|----------|-------------------|---------|
| **organisationName** / **organizationName** / **name** | Text | Organization name | Yes | - | `ABC Pharmaceutical` |
| **label** | Text | Organization label | No | - | `ABC Pharma` |
| **organisationType** / **organizationType** / **type** | CDISC CT | Type of organization | Yes | C188724 | `Pharmaceutical Company` |
| **organisationIdentifierScheme** / **organizationIdentifierScheme** / **identifierScheme** | Text | Identifier scheme | Yes | - | `DUNS` |
| **organisationIdentifier** / **organizationIdentifier** / **identifier** | Text | Organization identifier | Yes | - | `123456789` |
| **organisationAddress** / **organizationAddress** / **address** | Address | Organization address | No | - | `123 Main St, City, State, 12345, USA` |

**Valid organizationType values (C188724):**
- `Clinical Study Registry` (C93453)
- `Regulatory Agency` (C188863)
- `Healthcare Facility` (C21541)
- `Pharmaceutical Company` (C54149)
- `Laboratory` (C37984)
- `Contract Research Organization` (C54148)
- `Government Institute` (C199144)
- `Academic Institution` (C18240)
- `Medical Device Company` (C215661)

**Address Format:**
- Format: `<street>, <city>, <state>, <postal_code>, <country>`
- Can also use pipe separator: `<street> | <city> | <state> | <postal_code> | <country>`
- Example: `123 Main Street, Boston, MA, 02101, USA`

### Study Identifiers Sheet

**Sheet Name:** `studyIdentifiers`

Defines study identifiers assigned by different organizations.

| Column | Format | Description | Required | Example |
|--------|--------|-------------|----------|---------|
| **studyIdentifier** / **identifier** | Text | Study identifier value | Yes | `ABC-123-2024` |
| **organization** | Organization Reference | Organization name that assigned the identifier | Yes | `ABC Pharmaceutical` |

**Notes:**
- The organization must exist in the Study Organizations sheet
- Multiple identifiers can be assigned by different organizations

### Study References Sheet

**Sheet Name:** `studyReferences`

Defines reference identifiers: identifiers of related documents/plans held
by an organization (`StudyVersion.referenceIdentifiers`).

| Column | Format | Description | Required | CDISC CT Code List | Example |
|--------|--------|-------------|----------|-------------------|---------|
| **studyIdentifier** / **identifier** | Text | Reference identifier value | Yes | - | `LZZT CD Plan 1` |
| **organization** | Organization Reference | Organization name holding the reference | Yes | - | `ABC Pharmaceutical` |
| **referenceType** | CDISC CT decode | Type of reference | Yes | C215478 | `Clinical Development Plan` |

**Notes:**
- The organization must exist in the Study Organizations sheet

### Documents Sheet

**Sheet Name:** `documents`

Defines study definition documents (protocols, etc.).

| Column | Format | Description | Required | CDISC CT Code List | Example |
|--------|--------|-------------|----------|-------------------|---------|
| **name** | Text | Document name | Yes | - | `Study Protocol v2.1` |
| **label** | Text | Document label | Yes | - | `Protocol` |
| **description** | Text | Document description | Yes | - | `Main study protocol document` |
| **type** | CDISC CT | Type of document | Yes | C215477 | `Protocol` |
| **templateName** | Text | Template name | Yes | - | `Standard Protocol Template` |
| **language** | ISO 639 Code | Document language | Yes | - | `en` |
| **documentVersion** | Document Version Reference | Name of the document version (documentVersions sheet) attached to this document | Yes | - | `PROTOCOL_V2` |
| **notes** | Note References | Comma-separated note names | No | - | `Protocol Note` |

**Valid type values (C215477):**
- `Protocol` (C70817)

### Document Versions Sheet

**Sheet Name:** `documentVersions`

Defines versions of the study definition documents, referenced by name from the documents sheet's `documentVersion` column.

| Column | Format | Description | Required | CDISC CT Code List | Example |
|--------|--------|-------------|----------|-------------------|---------|
| **name** | Text | Document version name (reference key) | Yes | - | `PROTOCOL_V2` |
| **version** | Text | Version identifier | Yes | - | `2.1` |
| **date** | Date Reference | Name of a governance date (dates sheet); the cell may be left empty | Yes | - | `Approval Date 1` |
| **status** | CDISC CT | Document version status | Yes | C188723 | `Final` |
| **sheetName** | Text | Name of the sheet holding the document version's narrative structure (see Document Template Sheets) | Yes | - | `m11Template` |
| **notes** | Note References | Comma-separated note names | No | - | `Protocol Note` |

### Abbreviation Sheet

**Sheet Name:** `abbreviations`

Defines abbreviations and acronyms used in the study.

| Column | Format | Description | Required | Example |
|--------|--------|-------------|----------|---------|
| **abbreviatedText** | Text | Abbreviation/acronym (reference key for the document `abbreviations` macro) | Yes | `AE` |
| **expandedText** | Text | Full definition | Yes | `Adverse Event` |
| **notes** | Note References | Comma-separated note names | No | `Definition Note` |

### Amendment Sheet

**Sheet Name:** `studyAmendments`

Defines study amendments.

| Column | Format | Description | Required | Example |
|--------|--------|-------------|----------|---------|
| **name** | Text | Amendment name | Yes | `Amendment 1` |
| **description** | Text | Amendment description | No | `Protocol amendment to modify inclusion criteria` |
| **label** | Text | Amendment label | No | `Amend-1` |
| **number** | Text | Amendment number | Yes | `01` |
| **summary** | Text | Amendment summary | Yes | `Modified inclusion criteria for better enrollment` |
| **date** | Date Reference | Name of a governance date (dates sheet, category `amendment`) | No | `Amendment 1 Approval` |
| **primaryReason** | CDISC CT / Other | Primary reason: CDISC CT value (C207415) or `Other = <text>` | Yes | `New Safety Information Available` |
| **secondaryReasons** | CDISC CT / Other | Comma-separated secondary reasons, same format as primaryReason; may be empty | Yes | `Protocol Design Error` |
| **enrollment** | Enrollment Values | Comma-separated: `GLOBAL: <n>`, `COUNTRY: <name>=<n>`, `REGION: <name>=<n>`, `COHORT: <cohort name>=<n>`, `SITE: <site name>=<n>` (cohort/site referenced by name, resolved once the designs are built) | Yes | `GLOBAL: 300, COHORT: T1DM=40` |
| **geographicScope** | Geographic Scopes | Comma-separated scopes: `Global`, `Region: <name>`, `Country: <name or code>` | No (defaults to `Global`) | `Global` |
| **template** | Text | Document template this amendment's changed sections apply to (case-insensitive match on the document's template name; defaults to `SPONSOR`) | No | `lilly` |
| **notes** | Note References | Comma-separated note names | No | `Amendment Note` |

### Amendment Changes Sheet

**Sheet Name:** `amendmentChanges`

Defines specific changes made in amendments.

| Column | Format | Description | Required | Example |
|--------|--------|-------------|----------|---------|
| **amendment** | Amendment Reference | Name (or legacy number) of the amendment the change belongs to | Yes | `Amendment 1` |
| **name** | Text | Change name | Yes | `Inclusion Criteria Change` |
| **description** | Text | Change description | Yes | `Modified age range from 18-65 to 18-75` |
| **label** | Text | Change label | No | `Age Change` |
| **rationale** | Text | Rationale for the change | Yes | `Improve enrollment` |
| **summary** | Text | Change summary | Yes | `Age range widened to 18-75` |
| **sections** | Section References | Comma-separated `<section number>: <section title>` pairs of the changed document sections | Yes | `5.1: Inclusion Criteria` |

### Amendment Impact Sheet

**Sheet Name:** `amendmentImpact`

Defines the impact of amendments.

| Column | Format | Description | Required | CDISC CT Code List | Example |
|--------|--------|-------------|----------|-------------------|---------|
| **amendment** | Amendment Reference | Name (or legacy number) of the amendment the impact belongs to | Yes | - | `Amendment 1` |
| **text** | Text | Impact description | Yes | - | `Assessment of safety implications` |
| **substantial** | Boolean | Substantial impact flag | Yes | - | `true` |
| **type** | CDISC CT | Type of impact | Yes | C215481 | `Study Subject Safety` |
| **notes** | Note References | Comma-separated note names | No | - | `Impact Note` |

**Valid type values (C215481):**
- `Study Subject Safety` (C215665)
- `Study Subject Rights` (C215666)
- `Study Data Reliability` (C215667)
- `Study Data Robustness` (C215668)

### Assigned Person Sheet

**Sheet Name:** `people`

Defines persons assigned to study roles (referenced by name from the roles sheet's `people` column).

| Column | Format | Description | Required | Example |
|--------|--------|-------------|----------|---------|
| **name** | Text | Person name (reference key) | Yes | `PERSON_1` |
| **description** | Text | Person description | No | `Principal Investigator` |
| **label** | Text | Person label | No | `PI Smith` |
| **personName** | Text | Structured name, two forms (see below) | No | `Dr, Fred, Smith, MD` |
| **jobTitle** | Text | Job title | Yes | `Principal Investigator` |
| **organization** | Organization Reference | Organization name | No | `Site Hospital` |

**personName forms:**
- Comma form (`prefixes, givenName..., familyName, suffixes`, at least 4 comma-separated parts): prefixes and suffixes are space-separated within their part and may be empty, e.g. `Dr, Fred, Smith, MD` or `, Fred, Smith,`. This is the legacy dialect; the exporter uses it whenever prefixes or suffixes are present.
- Simple form (`First [Middle...] Last`): last word is the family name, the rest are given names.

### Roles Sheet

**Sheet Name:** `roles`

Defines study roles, linking organizations and assigned persons to a role code.

| Column | Format | Description | Required | Example |
|--------|--------|-------------|----------|---------|
| **name** | Text | Role name | Yes | `ROLE_1` |
| **description** | Text | Role description | No | `Sponsor role` |
| **label** | Text | Role label | No | `Sponsor` |
| **role** | CDISC CT | Role code | Yes | `Sponsor` |
| **organizations** | Organization References | Comma-separated organization names (studyOrganizations sheet) | No | `ABC Pharmaceutical` |
| **people** | Person References | Comma-separated assigned person names (people sheet) | No | `PERSON_1` |
| **masking** | Text | Masking description; the role is marked as masked when non-empty | No | `Blinded to treatment` |
| **notes** | Note References | Comma-separated note names | No | `Role Note` |

### Document Content Sheet

**Sheet Name:** `documentContent`

Defines content sections within documents.

| Column | Format | Description | Required | Example |
|--------|--------|-------------|----------|---------|
| **name** | Text | Content item name (referenced from the template sheets' `content` column) | Yes | `Inclusion Criteria` |
| **text** | HTML/Text | Content text | Yes | `<div>Patients must be 18-75 years old</div>` |

**Notes:**
- Text content is automatically wrapped in `<div>` tags if not already HTML
- Supports HTML formatting for rich content
- Text is sanitised to valid XHTML on import (CDISC CORE rules CORE-001069 /
  CORE-000945): disallowed elements (`<style>`, `<script>`, `<meta>`,
  `<link>`) and attributes (e.g. `type` on `<ol>`/`<ul>`/`<li>`) are removed
  and malformed tags repaired, each fix logged as a warning
- `<usdm:macro/>` tags in the text are expanded after the full study is
  assembled. Supported macro ids: `xref` (reference to a named object's
  attribute, path syntax supported), `element` (study elements such as
  `study_full_title`, `study_identifier`, `study_phase`), `section`
  (generated sections `title_page`, `inclusion`, `exclusion`,
  `objective_endpoints` with `template="m11"` or `"plain"`, plus `soa` /
  `timeline` for the plain template), `image` (file inlined as base64 from
  the workbook's folder), `note`, `bc` and `abbreviations`. Expanded content
  carries `<usdm:ref/>` references, matching the legacy `usdm` package

### Document Template Sheets

**Sheet Name:** _not fixed_ — one sheet per document template. The sheet name is given by the `sheetName` column of the documentVersions sheet or, for legacy workbooks, by a `TEMPLATE <name> = <sheet name>` mapping on the configuration sheet (the legacy default maps the `SPONSOR` template to a sheet named `document`).

Defines the section structure (table of contents) of a document version, each row referencing a content item from the documentContent sheet.

| Column | Format | Description | Required | Example |
|--------|--------|-------------|----------|---------|
| **name** | Text | Section name (defaults to `SECTION <number>` when empty) | Yes | `Introduction` |
| **sectionNumber** | Text | Dotted section number; the dot depth defines the section level | Yes | `1.2` |
| **sectionTitle** | Text | Section title | Yes | `Trial Design` |
| **displaySectionNumber** | Boolean | Display the section number | Yes | `true` |
| **displaySectionTitle** | Boolean | Display the section title | Yes | `true` |
| **content** | Content Reference | Name of the narrative content item (documentContent sheet) | Yes | `Inclusion Criteria` |

### Eligibility Criteria Items Sheet

**Sheet Name:** `eligibilityCriteriaItems`

Defines individual eligibility criteria items.

| Column | Format | Description | Required | Example |
|--------|--------|-------------|----------|---------|
| **name** | Text | Criteria item name | Yes | `Age Range` |
| **description** | Text | Criteria description | Yes | `Patient age must be within specified range` |
| **label** | Text | Criteria label | Yes | `Age` |
| **text** | Text | Criteria text | Yes | `Age 18-75 years inclusive` |
| **dictionary** | Dictionary Reference | Dictionary name for parameterized text; may be empty | Yes | `Age Template` |

### Extensions Sheet

**Sheet Name:** `extensions`

Defines USDM extension attributes attached to the study version or to a study identifier, using a two-column name/value format.

| Column | Format | Description | Required | Example |
|--------|--------|-------------|----------|---------|
| **name** | Text | `<parent>: <url>` where `<parent>` is `StudyVersion` or `StudyIdentifier=<identifier text>` and `<url>` is the extension URL | Yes | `StudyVersion: http://example.com/ext/sponsor-flag` |
| **value** | Text | `<type>: <payload>` where `<type>` is `string`, `boolean`, `integer`, `id` or `code` (code payload: `<code>, system: <s>, version: <v>, decode: <d>`); empty for a valueless attribute | No | `string: some value` |

### Sites Sheet

**Sheet Name:** `studyDesignSites`

Defines study sites, each managed by an organization from the studyOrganizations sheet.

| Column | Format | Description | Required | Example |
|--------|--------|-------------|----------|---------|
| **siteName** / **name** | Text | Site name | Yes | `Memorial Hospital` |
| **siteDescription** / **description** | Text | Site description | Yes | `Primary research site` |
| **siteLabel** / **label** | Text | Site label | Yes | `Site 001` |
| **siteCountry** / **country** | ISO 3166 Code | Site country (code or name) | Yes | `USA` |
| **organization** | Organization Reference | Managing organization name | Yes | `Site Hospital` |

## Study Design Sheets

### Study Design Sheet

**Sheet Name:** `studyDesign`

Defines a study design using a key-value format (Column A = key, Column B = value), followed by the arm/epoch design matrix. In the multi-workbook format this sheet lives in each design workbook; in the legacy format it is embedded in the main workbook.

| Key | Format | Description | Required | CDISC CT Code List | Example |
|-----|--------|-------------|----------|-------------------|---------|
| **studyDesignName** / **name** | Text | Study design name | Yes | - | `Study Design 1` |
| **studyDesignDescription** / **description** | Text | Study design description | Yes | - | `The main study design` |
| **label** | Text | Study design label | No | - | `Main Design` |
| **studyDesignType** / **studyType** | CDISC CT | Study type; `Observational Study` switches the design (and the sub type / time perspective / sampling method codelists) to observational | Yes | C99077 | `Interventional Study` |
| **studyDesignPhase** / **studyPhase** | CDISC CT | Trial phase | Yes | C66737 | `Phase III Trial` |
| **studyDesignRationale** | Text | Design rationale | Yes | - | `Randomized to remove bias` |
| **therapeuticAreas** | Codes | Comma-separated therapeutic area codes | No | - | `SPONSOR: A1=Diabetes` |
| **studyDesignBlindingScheme** | CDISC CT | Blinding schema (interventional) | No | C66735 | `Double Blind Study` |
| **trialIntentTypes** | CDISC CT | Comma-separated intent types (interventional) | No | C66736 | `Treatment Study` |
| **trialSubTypes** | CDISC CT | Comma-separated trial sub types | No | C66739 / C215486 | `Efficacy Study` |
| **interventionModel** / **model** | CDISC CT | Intervention model | Yes | C99076 | `Parallel Study` |
| **mainTimeline** | Sheet Reference | Sheet name of the main timeline (SoA) sheet | Yes | - | `mainTimeline` |
| **otherTimelines** | Sheet References | Comma-separated sheet names of additional timeline sheets | No | - | `adverseEvents, followUp` |
| **specimenRetentions** | Specimen References | Comma-separated names from the studyDesignSpecimen sheet | No | - | `SPECIMEN_1` |
| **timePerspective** | CDISC CT | Time perspective (observational) | No | C127261 | `Prospective` |
| **samplingMethod** | CDISC CT | Sampling method (observational) | No | C127260 | `Probability Sampling` |
| **characteristics** | CDISC CT | Comma-separated design characteristics | No | C207416 | `Randomized` |

The legacy `masking` key is recognised but ignored with a warning (masking moved to the roles sheet).

**Design matrix:** a blank key row ends the key/value block. The next row is a header row listing epoch names (studyDesignEpochs sheet) from column B onwards; each following row starts with an arm name (studyDesignArms sheet) in column A, and each body cell holds a comma-separated list of element names (studyDesignElements sheet) for that arm/epoch combination.

### Study Design Arms Sheet

**Sheet Name:** `studyDesignArms`

Defines study arms/groups for the clinical trial.

| Column | Format | Description | Required | CDISC CT Code List | Example |
|--------|--------|-------------|----------|-------------------|---------|
| **studyArmName** / **name** | Text | Study arm name | Yes | - | `Treatment Arm A` |
| **studyArmDescription** / **description** | Text | Study arm description | Yes | - | `Active treatment with Drug X` |
| **label** | Text | Study arm label | No | - | `Arm A` |
| **studyArmType** / **type** | CDISC CT | Type of study arm | Yes | C174222 | `Investigational Arm` |
| **studyArmDataOriginDescription** / **dataOriginDescription** | Text | Data origin description | Yes | - | `Data collected during study` |
| **studyArmDataOriginType** / **dataOriginType** | CDISC CT | Type of data origin | Yes | C188727 | `Data Generated Within Study` |
| **notes** | Note References | Comma-separated note names | No | - | `Safety Note 1, Efficacy Note` |

**Valid studyArmType values (C174222):**
- `Investigational Arm` (C174266)
- `Active Comparator Arm` (C174267)
- `Placebo Control Arm` (C174268)
- `Protocol Treatment Arm` (C15538)
- `Sham Comparator Arm` (C174269)
- `No Intervention Arm` (C174270)
- `Control Arm` (C174226)

**Valid studyArmDataOriginType values (C188727):**
- `Historical Data` (C188864)
- `Data Generated Within Study` (C188866)
- `Real-world Data` (C165830)
- `Synthetic Data` (C176263)
- `Virtual Data` (C188865)

### Study Design Characteristics Sheet

**Sheet Name:** `studyDesignCharacteristics`

Defines study design characteristics.

| Column | Format | Description | Required | Example |
|--------|--------|-------------|----------|---------|
| **name** | Text | Characteristic name | Yes | `Randomization` |
| **description** | Text | Characteristic description | Yes | `Study uses randomization` |
| **label** | Text | Characteristic label | Yes | `Randomized` |
| **text** | Text | Characteristic text/details | Yes | `Subjects randomized 1:1` |
| **dictionary** | Dictionary Reference | Dictionary name for parameterized text; may be empty | Yes | `Randomization Template` |
| **notes** | Note References | Comma-separated note names | No | `Randomization Note` |

### Study Design Encounters Sheet

**Sheet Name:** `studyDesignEncounters`

Defines study encounters/visits.

| Column | Format | Description | Required | CDISC CT Code List | Example |
|--------|--------|-------------|----------|-------------------|---------|
| **encounterName** / **name** | Text | Encounter name | Yes | - | `Screening Visit` |
| **encounterDescription** / **description** | Text | Encounter description | Yes | - | `Initial screening procedures` |
| **label** | Text | Encounter label | No | - | `Visit 1` |
| **encounterType** / **type** | CDISC CT | Type of encounter; may be empty | Yes | C188728 | `Visit` |
| **encounterEnvironmentalSetting** / **environmentalSettings** / **environmentalSetting** | CDISC CT | Environmental settings (multiple) | Yes | C127262 | `Clinic, Hospital` |
| **encounterContactModes** / **contactModes** | CDISC CT | Contact modes (multiple) | Yes | C171445 | `In Person, Telephone Call` |
| **transitionStartRule** | Text | Start transition rule | No | - | `After consent signed` |
| **transitionEndRule** | Text | End transition rule | No | - | `All procedures completed` |
| **window** | Timing Reference | Name of the timing (studyDesignTiming sheet) the encounter is scheduled at | No | - | `T1` |

**Valid type values (C188728):**
- `Visit` (C25716)

**Valid environmentalSettings values (C127262):**
- `Household Environment` (C102647)
- `Childcare Center` (C127785)
- `Ambulatory Care Facility` (C16281)
- `Hospital` (C16696)
- `Healthcare Facility` (C21541)
- `Clinic` (C211570)
- `Home` (C18002)
- And many more...

**Valid contactModes values (C171445):**
- `Text Message` (C157352)
- `Audio-Videoconferencing` (C171525)
- `In Person` (C175574)
- `E-mail` (C25170)
- `Telephone Call` (C171537)
- And more...

### Study Design Epochs Sheet

**Sheet Name:** `studyDesignEpochs`

Defines study epochs/periods.

| Column | Format | Description | Required | CDISC CT Code List | Example |
|--------|--------|-------------|----------|-------------------|---------|
| **studyEpochName** / **name** | Text | Epoch name | Yes | - | `Treatment Period` |
| **studyEpochDescription** / **description** | Text | Epoch description | Yes | - | `Active treatment phase` |
| **label** | Text | Epoch label | No | - | `Treatment` |
| **studyEpochType** / **type** | CDISC CT | Type of epoch | Yes | C99079 | `Treatment Epoch` |
| **notes** | Note References | Comma-separated note names | No | - | `Treatment Note` |

**Valid type values (C99079):**
- `Open Label Treatment Epoch` (C102256)
- `Product Exposure Epoch` (C210380)
- `Screening Epoch` (C202487)
- `Treatment Epoch` (C101526)
- `Follow-Up Epoch` (C202578)
- `Baseline Epoch` (C125938)
- `Washout Period` (C42872)
- And more...

### Study Design Elements Sheet

**Sheet Name:** `studyDesignElements`

Defines study elements, referenced from the studyDesign sheet's design matrix.

| Column | Format | Description | Required | Example |
|--------|--------|-------------|----------|---------|
| **studyElementName** / **name** | Text | Element name | Yes | `Screening` |
| **studyElementDescription** / **description** | Text | Element description | Yes | `Subject screening element` |
| **label** | Text | Element label | No | `Screen` |
| **transitionStartRule** | Text | Start transition rule | No | `After consent signed` |
| **transitionEndRule** | Text | End transition rule | No | `All procedures completed` |
| **notes** | Note References | Comma-separated note names | No | `Element Note` |

### Study Design Activities Sheet

**Sheet Name:** `studyDesignActivities`

Defines study activities, referenced by name from the timeline (SoA) sheets.

| Column | Format | Description | Required | Example |
|--------|--------|-------------|----------|---------|
| **activityName** / **name** | Text | Activity name | Yes | `Blood Sample` |
| **activityDescription** / **description** | Text | Activity description | Yes | `Collect blood sample` |
| **label** | Text | Activity label | No | `Blood` |
| **notes** | Note References | Comma-separated note names | No | `Activity Note` |

### Study Design Timing Sheet

**Sheet Name:** `studyDesignTiming`

Defines timings between the timepoints of the timeline (SoA) sheets.

| Column | Format | Description | Required | CDISC CT Code List | Example |
|--------|--------|-------------|----------|-------------------|---------|
| **name** | Text | Timing name | Yes | - | `T1` |
| **description** | Text | Timing description | Yes | - | `Screening to dosing` |
| **label** | Text | Timing label | Yes | - | `Dose timing` |
| **type** | Text | `Fixed`, `After` or `Before` | Yes | C201264 | `After` |
| **timingValue** | Duration | Value and units, encoded as an ISO 8601 duration | Yes | - | `7 days` |
| **toFrom** | Text | Relative to/from: `S2S`, `S2E`, `E2S` or `E2E` (defaults to `S2S`) | Yes | C201265 | `S2S` |
| **from** | Timepoint Reference | Name of the timepoint the timing is relative from | Yes | - | `V1` |
| **to** | Timepoint Reference | Name of the timepoint the timing is relative to | Yes | - | `V2` |
| **window** | Window | Timing window as `<lower>..<upper> <units>`; may be empty | Yes | - | `-1..1 days` |

### Timeline (SoA) Sheets

**Sheet Name:** _not fixed_ — one sheet per timeline, named by the studyDesign sheet's `mainTimeline` and `otherTimelines` rows.

Each timeline sheet uses a fixed grid layout rather than a named-column table.

**Timeline metadata (column B, rows 1-6):**

| Row | Content | Example |
|-----|---------|---------|
| 1 | Timeline name | `MAIN_TIMELINE` |
| 2 | Timeline description | `Main study schedule` |
| 3 | Entry condition | `Subject consented` |
| 4 | Planned duration (quantity) | `26 weeks` |
| 5 | Reason duration will vary (empty when it will not) | `Response dependent` |
| 6 | Duration description | `Around 26 weeks` |

**Timepoint columns (column D onwards, rows 1-8):** each column defines a scheduled instance:

| Row | Content | Example |
|-----|---------|---------|
| 1 | Timepoint name (reference key for timings and defaults) | `V1` |
| 2 | Timepoint description | `Visit 1` |
| 3 | Timepoint label | `Visit 1` |
| 4 | Instance type: `Activity` or `Decision` | `Activity` |
| 5 | Default reference: name of the next timepoint, or `(EXIT)` for a timeline exit; may be empty | `V2` |
| 6 | Condition assignments (decision instances) | `if positive: V3` |
| 7 | Epoch name (studyDesignEpochs sheet) | `Treatment Period` |
| 8 | Encounter name (studyDesignEncounters sheet) | `Screening Visit` |

**Optional timeline row (row 9):** when the cell in column C of row 9 reads `timeline`, the row carries per-column timeline references (`ScheduledActivityInstance.timelineId`, resolved by timeline name with a legacy sheet-name fallback) and the header row and activity rows sit one row lower. The exporter always writes this row; legacy workbooks may omit it.

**Activity rows (from row 10, or row 11 when the timeline row is present, below a header row):** column A holds the parent activity name, column B the (child) activity name (studyDesignActivities sheet), column C comma-separated biomedical concept / procedure / timeline references for the activity, and each timepoint column an `X` marking the activity as performed at that timepoint.

### Study Design Populations Sheet

**Sheet Name:** `studyDesignPopulations`

Defines the study design population and its cohorts. Exactly one row must have level `MAIN`; the remaining rows define cohorts.

| Column | Format | Description | Required | Example |
|--------|--------|-------------|----------|---------|
| **level** | Text | `MAIN` for the design population, `COHORT` (anything else) for a cohort | Yes | `MAIN` |
| **name** | Text | Population/cohort name | Yes | `POP1` |
| **description** | Text | Population/cohort description | Yes | `Full study population` |
| **label** | Text | Population/cohort label | Yes | `Population` |
| **plannedEnrollmentNumber** | Quantity / Range | Planned enrollment, quantity or `<low>..<high>` range | Yes | `300` |
| **plannedCompletionNumber** | Quantity / Range | Planned completion, quantity or `<low>..<high>` range | Yes | `250..280` |
| **plannedAge** | Range | Planned age range with units; may be empty | Yes | `18..75 years` |
| **plannedSexOfParticipants** | CDISC CT | Up to two comma-separated values (C66732); may be empty | Yes | `Female, Male` |
| **includesHealthySubjects** | Boolean | Healthy subjects included flag | No | `true` |
| **characteristics** | Characteristic References | Comma-separated names (studyDesignCharacteristics sheet, cohorts only) | No | `Randomization` |
| **indications** | Indication References | Comma-separated names (studyDesignIndications sheet, cohorts only) | No | `Diabetes` |

### Study Design Objectives and Endpoints Sheet

**Sheet Name:** `studyDesignOE`

Defines objectives and their endpoints, one row per endpoint. A row with a non-empty `objectiveText` starts a new objective; following rows attach further endpoints to it. Rows with no endpoint content are objective-only.

| Column | Format | Description | Required | CDISC CT Code List | Example |
|--------|--------|-------------|----------|-------------------|---------|
| **objectiveXref** / **objectiveName** | Text | Objective name | Yes | - | `OBJ1` |
| **objectiveDescription** | Text | Objective description | No | - | `Primary efficacy objective` |
| **objectiveLabel** | Text | Objective label | No | - | `Primary Objective` |
| **objectiveText** | Text | Objective text; non-empty starts a new objective | No | - | `To assess the efficacy of Drug X` |
| **objectiveLevel** | CDISC CT | Objective level | Yes | C188725 | `Trial Primary Objective` |
| **objectiveDictionary** | Dictionary Reference | Dictionary name for parameterized text | No | - | `Objective Template` |
| **endpointXref** / **endpointName** | Text | Endpoint name | No | - | `EP1` |
| **endpointDescription** | Text | Endpoint description | No | - | `Change from baseline` |
| **endpointLabel** | Text | Endpoint label | No | - | `Primary Endpoint` |
| **endpointText** | Text | Endpoint text | No | - | `Change from baseline in HbA1c at week 26` |
| **endpointPurposeDescription** / **endpointPurpose** | Text | Endpoint purpose (defaults to `None provided`) | No | - | `Efficacy` |
| **endpointLevel** | CDISC CT | Endpoint level | Yes | C188726 | `Primary Endpoint` |
| **endpointDictionary** | Dictionary Reference | Dictionary name for parameterized text | No | - | `Endpoint Template` |

### Eligibility Criteria Sheet

**Sheet Name:** `eligibilityCriteria`

Defines the design-level eligibility criteria (preferred split dialect), each referencing a criterion item from the eligibilityCriteriaItems sheet.

| Column | Format | Description | Required | CDISC CT Code List | Example |
|--------|--------|-------------|----------|-------------------|---------|
| **category** | CDISC CT | Inclusion/exclusion category | Yes | C66797 | `Inclusion Criteria` |
| **identifier** | Text | Criterion identifier | Yes | - | `IE01` |
| **name** | Text | Criterion name | Yes | - | `Age Range` |
| **description** | Text | Criterion description | Yes | - | `Patient age must be within range` |
| **label** | Text | Criterion label | Yes | - | `Age` |
| **item** | Item Reference | Name of the criterion item (eligibilityCriteriaItems sheet) | Yes | - | `Age Range Item` |

### Study Design Eligibility Criteria Sheet (Legacy)

**Sheet Name:** `studyDesignEligibilityCriteria`

The legacy one-sheet eligibility form: one row per criterion carrying both the criterion fields and the item text. Each row creates a criterion item (named `<name>-ITEM`) and a criterion referencing it. Read as a fallback when the split eligibilityCriteria / eligibilityCriteriaItems sheets are absent.

| Column | Format | Description | Required | CDISC CT Code List | Example |
|--------|--------|-------------|----------|-------------------|---------|
| **category** | CDISC CT | Inclusion/exclusion category | Yes | C66797 | `Exclusion Criteria` |
| **identifier** | Text | Criterion identifier | Yes | - | `IE01` |
| **name** | Text | Criterion name | Yes | - | `Age Range` |
| **description** | Text | Criterion description | No | - | `Patient age must be within range` |
| **label** | Text | Criterion label | No | - | `Age` |
| **text** | Text | Criterion text | Yes | - | `Age 18-75 years inclusive` |
| **dictionary** | Dictionary Reference | Dictionary name for parameterized text | No | - | `Age Template` |

### Study Design Conditions Sheet

**Sheet Name:** `studyDesignConditions`

Defines conditions applying to activities, procedures or biomedical concepts.

| Column | Format | Description | Required | Example |
|--------|--------|-------------|----------|---------|
| **name** | Text | Condition name | Yes | `COND1` |
| **description** | Text | Condition description | Yes | `Only if clinically indicated` |
| **label** | Text | Condition label | Yes | `Clinically indicated` |
| **text** | Text | Condition text | Yes | `Perform only if clinically indicated` |
| **context** | References | Comma-separated names of scheduled activity instances or activities the condition applies within; may be empty | Yes | `V1, Blood Sample` |
| **appliesTo** | References | Comma-separated names of procedures, activities or biomedical concepts the condition applies to; may be empty when a context is given | Yes | `Blood Sample` |

### Study Design Interventions Sheet

**Sheet Name:** `studyDesignInterventions`

Defines study interventions and their administrations.

| Column | Format | Description | Required | CDISC CT Code List | Example |
|--------|--------|-------------|----------|-------------------|---------|
| **name** | Text | Intervention name | Yes | - | `Drug X Treatment` |
| **description** | Text | Intervention description | No | - | `Active treatment with Drug X` |
| **label** | Text | Intervention label | No | - | `Drug X` |
| **type** | CDISC CT | Type of intervention | Yes | C99078 | `Pharmacologic Substance` |
| **role** | CDISC CT | Role of intervention; may be empty | Yes | C207417 | `Experimental Intervention` |
| **codes** | Codes | Additional codes; may be empty | Yes | - | `C1909:Pharmacologic Substance` |
| **minimumResponseDuration** | Quantity | Minimum response duration; may be empty | Yes | - | `4 weeks` |
| **administrationName** | Text | Administration name; empty when the row carries no administration | Yes | - | `Daily Oral Dose` |
| **administrationDescription** | Text | Administration description | No | - | `Once daily oral administration` |
| **administrationLabel** | Text | Administration label | No | - | `QD PO` |
| **administrationDose** | Quantity | Administration dose; may be empty | Yes | - | `10 mg` |
| **administrationRoute** | CDISC CT | Route of administration; may be empty | Yes | C66729 | `Oral` |
| **administrationFrequency** | CDISC CT | Frequency of administration; may be empty | Yes | C71113 | `Once Daily` |
| **product** | Product Reference | Product name (studyProducts sheet) | No | - | `Drug X Tablet` |
| **administrationDurationDescription** | Text | Duration description | No | - | `Treatment continues for 12 weeks` |
| **administrationDurationWillVary** | Boolean | Duration will vary flag | Yes | - | `false` |
| **administrationDurationWillVaryReason** | Text | Reason duration varies; may be empty | Yes | - | `Based on response` |
| **administrationDurationQuantity** | Quantity | Duration quantity; may be empty | Yes | - | `12 weeks` |

**Valid type values (C99078):**
- `Gene Therapy` (C15238)
- `Biological Agent` (C307)
- `Radiation Therapy` (C15313)
- `Medical Device` (C16830)
- `Diagnostic Procedure` (C18020)
- `Pharmacologic Substance` (C1909)
- `Combination Product` (C54696)
- `Dietary Supplement` (C1505)
- `Behavioral Intervention` (C15184)
- `Physical Medical Procedure` (C98769)

**Valid role values (C207417):**
- `Additional Required Treatment` (C207614)
- `Background Treatment` (C165822)
- `Challenge Agent` (C158128)
- `Diagnostic` (C18020)
- `Experimental Intervention` (C41161)
- `Placebo` (C753)
- `Rescue Medicine` (C165835)
- `Active Comparator` (C68609)

### Study Design Procedures Sheet

**Sheet Name:** `studyDesignProcedures`

Defines procedures, referenced from the timeline (SoA) sheets' activity rows.

| Column | Format | Description | Required | Example |
|--------|--------|-------------|----------|---------|
| **procedureName** / **name** | Text | Procedure name | Yes | `Blood Draw` |
| **procedureDescription** / **description** | Text | Procedure description | Yes | `Venous blood draw` |
| **label** | Text | Procedure label | No | `Blood Draw` |
| **procedureType** | Text | Procedure type | Yes | `Diagnostic` |
| **procedureCode** / **code** | Code | Procedure code; may be empty | Yes | `SPONSOR: BD1=Blood Draw` |

### Study Design Estimands Sheet

**Sheet Name:** `studyDesignEstimands`

Defines estimands, one row per intercurrent event. A row with a non-empty `summaryMeasure` starts a new estimand; following rows attach further intercurrent events to it (empty event name/description repeat the previous row's values).

| Column | Format | Description | Required | Example |
|--------|--------|-------------|----------|---------|
| **name** / **xref** | Text | Estimand name (`xref` is the legacy heading) | Yes | `ESTIMAND1` |
| **summaryMeasure** | Text | Population summary measure; non-empty starts a new estimand | Yes | `Difference in mean change from baseline` |
| **populationDescription** | Text | Analysis population description | Yes | `All randomized subjects` |
| **populationSubset** | Population Reference | Name of the population or cohort (studyDesignPopulations sheet) the analysis population subsets | Yes | `POP1` |
| **intercurrentEventName** | Text | Intercurrent event name | Yes | `ICE1` |
| **intercurrentEventDescription** / **description** | Text | Intercurrent event description | Yes | `Discontinuation of treatment` |
| **label** | Text | Intercurrent event label | No | `Discontinuation` |
| **intercurrentEventStrategy** | Text | Intercurrent event strategy | Yes | `Treatment policy` |
| **intercurrentEventText** | Text | Intercurrent event text | Yes | `Subjects discontinuing treatment` |
| **treatment** / **treatmentXref** | Intervention Reference | Name of the intervention (studyDesignInterventions sheet) | Yes | `Drug X Treatment` |
| **endpoint** / **endpointXref** | Endpoint Reference | Name of the endpoint (studyDesignOE sheet) | Yes | `EP1` |

### Study Design Indications Sheet

**Sheet Name:** `studyDesignIndications`

Defines study indications.

| Column | Format | Description | Required | Example |
|--------|--------|-------------|----------|---------|
| **name** | Text | Indication name | Yes | `IND1` |
| **description** | Text | Indication description | Yes | `Type 2 diabetes mellitus` |
| **label** | Text | Indication label | No | `T2DM` |
| **isRareDisease** | Boolean | Rare disease flag | No | `false` |
| **codes** | Codes | Comma-separated codes; may be empty | Yes | `SNOMED: 44054006=Diabetes mellitus type 2` |
| **notes** | Note References | Comma-separated note names | No | `Indication Note` |

### Study Design Specimen Sheet

**Sheet Name:** `studyDesignSpecimen`

Defines biospecimen retentions, referenced from the studyDesign sheet's `specimenRetentions` row.

| Column | Format | Description | Required | Example |
|--------|--------|-------------|----------|---------|
| **name** | Text | Specimen retention name (reference key) | Yes | `SPECIMEN_1` |
| **description** | Text | Specimen retention description | No | `Serum samples retained` |
| **label** | Text | Specimen retention label | No | `Serum` |
| **retained** | Boolean | Specimen is retained | Yes | `true` |
| **includesDNA** | Boolean | Retention includes DNA | Yes | `false` |

### Study Products Sheet

**Sheet Name:** `studyProducts`

Defines administrable products with their ingredients, substances and strengths. A row with a non-empty `name` starts a new product; following rows add further ingredients/strengths to it. Reference substance columns describe the substance a strength is expressed relative to.

| Column | Format | Description | Required | CDISC CT Code List | Example |
|--------|--------|-------------|----------|-------------------|---------|
| **name** | Text | Product name; empty continues the previous product | Yes | - | `Drug X Tablet` |
| **description** | Text | Product description | No | - | `Drug X 10 mg film-coated tablet` |
| **label** | Text | Product label | No | - | `Drug X` |
| **administrableDoseForm** | CDISC CT | Dose form | Yes | C66726 | `Tablet` |
| **productDesignation** | CDISC CT | Product designation | Yes | C207418 | `Investigational Product` |
| **pharmacologicClass** | Code | Pharmacologic class code; may be empty | Yes | - | `MED-RT: N0000175565=Kinase Inhibitor` |
| **productSourcing** | CDISC CT | Product sourcing; may be empty | Yes | - | `Centrally Sourced` |
| **substanceName** | Text | Ingredient substance name; empty when the row carries no ingredient | Yes | - | `Substance X` |
| **substanceDescription** | Text | Substance description | No | - | `Active substance` |
| **substanceLabel** | Text | Substance label | No | - | `Substance X` |
| **substanceCode** | Code | Substance code; may be empty | Yes | - | `UNII: ABC123=Substance X` |
| **ingredientRole** | Code | Ingredient role code; may be empty | Yes | - | `SPONSOR: A=Active Ingredient` |
| **strengthName** | Text | Strength name; empty when the row carries no strength | Yes | - | `STRENGTH_1` |
| **strengthdescription** | Text | Strength description (note the lowercase `d`) | No | - | `10 mg per tablet` |
| **strengthLabel** | Text | Strength label | No | - | `10 mg` |
| **strengthNumerator** | Quantity / Range | Strength numerator, quantity or `<low>..<high>` range | Yes | - | `10 mg` |
| **strengthDenominator** | Quantity | Strength denominator | Yes | - | `1 tablet` |
| **referenceSubstanceName** | Text | Reference substance name; empty when none | Yes | - | `Substance X Base` |
| **referenceSubstanceDescription** | Text | Reference substance description | No | - | `Base form` |
| **referenceSubstanceLabel** | Text | Reference substance label | No | - | `Base` |
| **referenceSubstanceCode** | Code | Reference substance code; may be empty | Yes | - | `UNII: DEF456=Substance X Base` |
| **referenceSubstanceStrengthName** | Text | Reference substance strength name; empty when none | Yes | - | `REF_STRENGTH_1` |
| **referenceSubstanceStrengthdescription** | Text | Reference substance strength description (note the lowercase `d`) | No | - | `9 mg base per tablet` |
| **referenceSubstanceStrengthLabel** | Text | Reference substance strength label | No | - | `9 mg` |
| **referenceSubstanceStrengthNumerator** | Quantity / Range | Reference strength numerator, quantity or range | Yes | - | `9 mg` |
| **referenceSubstanceStrengthDenominator** | Quantity | Reference strength denominator | Yes | - | `1 tablet` |

### Study Product Organization Roles Sheet

**Sheet Name:** `studyProductOrganizationRoles`

Defines organization roles for products and devices.

| Column | Format | Description | Required | Example |
|--------|--------|-------------|----------|---------|
| **name** | Text | Role name | Yes | `PROD_ROLE_1` |
| **description** | Text | Role description | No | `Manufacturer of Drug X` |
| **label** | Text | Role label | No | `Manufacturer` |
| **organization** | Organization Reference | Organization name (studyOrganizations sheet) | Yes | `ABC Pharmaceutical` |
| **role** | CDISC CT | Organization role code | Yes | `Manufacturer` |
| **appliesTo** | References | Comma-separated product or device names (studyProducts / studyDevices sheets) | No | `Drug X Tablet` |

### Study Devices Sheet

**Sheet Name:** `studyDevices`

Defines medical devices, each embedding a product from the studyProducts sheet.

| Column | Format | Description | Required | Example |
|--------|--------|-------------|----------|---------|
| **name** | Text | Device name | Yes | `Injection Pen` |
| **description** | Text | Device description | Yes | `Pre-filled injection pen` |
| **label** | Text | Device label | Yes | `Pen` |
| **hardwareVersion** | Text | Hardware version | Yes | `2.0` |
| **softwareVersion** | Text | Software version | Yes | `1.4.2` |
| **sourcing** | CDISC CT | Device sourcing | Yes | `Centrally Sourced` |
| **product** | Product Reference | Name of the embedded administrable product (studyProducts sheet) | Yes | `Drug X Tablet` |
| **notes** | Note References | Comma-separated note names | No | `Device Note` |

## Data Format Guidelines

### Text Fields
- Standard text fields accept any string value
- Leading and trailing whitespace is automatically trimmed
- Empty strings are treated as null/missing values

### CDISC CT Fields
- Use **preferred terms** (human-readable text) rather than C-codes
- System automatically looks up corresponding C-codes
- Case-sensitive matching
- Examples: `Investigational Arm`, `Treatment Epoch`, `Oral`

### Multiple Values
- Separate multiple values with commas
- Example: `In Person, Telephone Call`
- Whitespace around commas is automatically trimmed

### Quantity Fields
- Format: `<value> <unit>`
- Examples: `10 mg`, `4 weeks`, `2.5 hours`
- Units must be valid CDISC CT terms from C71620

### Boolean Fields
- Accepted true values: `true`, `TRUE`, `yes`, `YES`, `1`
- Accepted false values: `false`, `FALSE`, `no`, `NO`, `0`
- Empty values default to `false`

### Reference Fields
- Reference other objects by their `name` field
- Must match exactly (case-sensitive)
- Referenced objects must exist in the system

### Note References
- Reference notes defined in the Notes sheet
- Multiple notes separated by commas
- Example: `Safety Note 1, Efficacy Note`

## CDISC CT Code Lists

The following CDISC CT code lists are used throughout the system:

| Code List | Description | Used For |
|-----------|-------------|----------|
| C66726 | CDISC SDTM Dosage Form Terminology | AdministrableProduct.administrableDoseForm |
| C66729 | CDISC SDTM Route of Administration Terminology | Administration.route |
| C66732 | CDISC SDTM Sex of Study Group Terminology | StudyDesignPopulation.plannedSex |
| C66735 | CDISC SDTM Trial Blinding Schema Terminology | InterventionalStudyDesign.blindingSchema |
| C66736 | CDISC SDTM Trial Indication Type Terminology | InterventionalStudyDesign.intentTypes |
| C66737 | CDISC SDTM Trial Phase Terminology | StudyDesign.studyPhase |
| C66739 | CDISC SDTM Trial Type Terminology | InterventionalStudyDesign.subTypes |
| C66797 | CDISC SDTM Category for Inclusion And Or Exclusion Terminology | EligibilityCriteria.category |
| C71113 | CDISC SDTM Frequency Terminology | Administration.frequency |
| C71620 | CDISC SDTM Unit of Measure Terminology | Quantity.unit |
| C99076 | CDISC SDTM Intervention Model Terminology | StudyDesign.interventionModel |
| C99077 | CDISC SDTM Study Type Terminology | StudyDesign.studyType |
| C99078 | CDISC SDTM Intervention Type Terminology | StudyIntervention.type |
| C99079 | CDISC SDTM Epoch Terminology | StudyEpoch.type |
| C127260 | CDISC SDTM Observational Study Sampling Method Terminology | ObservationalStudyDesign.samplingMethod |
| C127261 | CDISC SDTM Observational Study Time Perspective Terminology | ObservationalStudyDesign.timePerspective |
| C127262 | CDISC SDTM Environmental Setting Terminology | Encounter.environmentalSettings |
| C171445 | CDISC SDTM Mode of Subject Contact Terminology | Encounter.contactModes |
| C174222 | CDISC Protocol Study Arm Type Value Set Terminology | StudyArm.type |
| C188723 | CDISC DDF Protocol Status Value Set Terminology | StudyProtocolVersion.protocolStatus |
| C188724 | CDISC DDF Organization Type Value Set Terminology | Organization.type |
| C188725 | CDISC DDF Objective Level Value Set Terminology | Objective.level |
| C188726 | CDISC DDF Endpoint Level Value Set Terminology | Endpoint.level |
| C188727 | CDISC DDF Study Arm Data Origin Type Value Set Terminology | StudyArm.dataOriginType |
| C188728 | CDISC DDF Encounter Type Value Set Terminology | Encounter.type |
| C201264 | CDISC DDF Timing Type Value Set Terminology | Timing.type |
| C201265 | CDISC DDF Timing Relative To From Value Set Terminology | Timing.relativeToFrom |
| C207412 | CDISC DDF Geographic Scope Type Value Set Terminology | GeographicScope.type |
| C207413 | CDISC DDF Governance Date Type Value Set Terminology | GovernanceDate.type |
| C207415 | CDISC DDF Study Amendment Reason Code Value Set Terminology | StudyAmendmentReason.code |
| C207416 | CDISC DDF Study Design Characteristics Value Set Terminology | StudyDesign.characteristics |
| C207417 | CDISC DDF Study Intervention Role Value Set Terminology | StudyIntervention.role |
| C207418 | CDISC DDF Study Intervention Product Designation Value Set Terminology | AdministrableProduct.productDesignation |
| C207419 | CDISC DDF Study Title Type Value Set Terminology | StudyTitle.type |

For complete lists of valid values, refer to the CDISC CT Excel file at `/Users/daveih/Documents/github/infographics/usdm_ct_infographic/ct.xlsx`.

## Notes

1. **Optional vs Required Sheets**: Most sheets are optional. If a sheet is not present, the system will continue processing other sheets.

2. **Error Handling**: The system provides detailed error messages with cell locations when validation fails.

3. **Cross-References**: Many fields reference objects defined in other sheets. Ensure referenced objects are created before they are referenced.

4. **CDISC CT Updates**: CDISC CT versions can be configured in the Configuration sheet. The system will use the specified versions for code validation.

5. **Extensibility**: Most CDISC CT code lists are extensible, meaning custom codes can be added beyond the standard terminology.

6. **Case Sensitivity**: CDISC CT preferred terms are case-sensitive and must match exactly as defined in the terminology.

# Building the Package
Build steps for deployment to pypi.org

- Build with `python3 -m build --sdist --wheel`
- Upload to pypi.org using `twine upload dist/*`
