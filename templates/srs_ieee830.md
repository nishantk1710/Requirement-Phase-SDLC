<!--
IEEE-830 (Karl Wiegers) SRS template — STRUCTURE & DELIVERY FORMAT ONLY.
Reproduces `Reference SRS format.docx` (format, not content). The G1 generator (P7)
fills each section from the approved requirement repository.

RULES:
 * EVERY section and subsection below MUST appear in the generated SRS, in this order.
   Where content is not yet available, emit "[TBD - Design/BA input]" and log it in Appendix C.
 * Fill modes: [REQUIREMENTS] from repo · [NARRATIVE] LLM draft else [TBD] · [TABLE] · [APPENDIX] · [TBD].
 * Formal ids assigned at generation: functional REQ-n · non-functional NFR-n · business BR-n.
 * "shall" = mandatory, "should" = desirable; each feature has Priority High|Medium|Low.
-->

# Software Requirements Specification
### for &lt;Project Name&gt;
&lt;Subtitle / one-line product description&gt;
Version &lt;x.y&gt; approved
Prepared by &lt;author, role&gt;
&lt;organization&gt;
&lt;date&gt;
*Document format based on the IEEE SRS template. Copyright © 1999 by Karl E. Wiegers.*

## Table of Contents
<!-- [TABLE] generated from the headings below -->

## Revision History
<!-- [TABLE] -->
| Name | Date | Reason For Changes | Version |
|---|---|---|---|
| &lt;name&gt; | &lt;date&gt; | &lt;reason&gt; | &lt;x.y&gt; |

## 1. Introduction
### 1.1 Purpose <!-- [NARRATIVE] -->
### 1.2 Document Conventions <!-- [NARRATIVE] REQ-n / NFR-n / BR-n; shall/should; feature priority; TBD -> Appendix C -->
### 1.3 Intended Audience and Reading Suggestions <!-- [NARRATIVE] -->
### 1.4 Product Scope <!-- [NARRATIVE] -->
### 1.5 References <!-- [TBD] list of referenced documents -->

## 2. Overall Description
### 2.1 Product Perspective <!-- [NARRATIVE] -->
### 2.2 Product Functions <!-- [NARRATIVE] summarised from Section 4 features -->
### 2.3 User Classes and Characteristics <!-- [TABLE] User Class | Characteristics -->
### 2.4 Operating Environment <!-- [NARRATIVE] -->
### 2.5 Design and Implementation Constraints <!-- [REQUIREMENTS] rtype = constraint -->
### 2.6 User Documentation <!-- [TBD] -->
### 2.7 Assumptions and Dependencies <!-- [REQUIREMENTS] rtype = assumption -->

## 3. External Interface Requirements
### 3.1 User Interfaces <!-- [NARRATIVE] design-system intro: theme, colour, typography, spacing, components -->
#### 3.1.1 Overall Visual Theme <!-- [NARRATIVE] visual identity / tone -->
#### 3.1.2 Colour Tokens <!-- [TABLE] Token | Hex | Usage -->
#### 3.1.3 Typography Tokens <!-- [TABLE] Token | Size | Weight | Usage -->
#### 3.1.4 Spacing, Radius, and Elevation Tokens <!-- [TABLE] Token | Value | Usage -->
#### 3.1.5 Layout and Interaction Standards <!-- [NARRATIVE] responsive, WCAG 2.1 AA, tap targets, nav, error handling -->
### 3.2 Hardware Interfaces <!-- [NARRATIVE] -->
### 3.3 Software Interfaces <!-- [TABLE] Interface | Description -->
### 3.4 Communications Interfaces <!-- [NARRATIVE] -->

## 4. System Features
<!-- [REQUIREMENTS] one 4.x subsection PER feature (grouped by `feature`). For each:
### 4.x <Feature name>
#### 4.x.1 Description and Priority
     <one-line description>  Priority: High | Medium | Low.
#### 4.x.2 Stimulus/Response Sequences
     - <stimulus> -> <system response>        ([TBD] if not derivable)
#### 4.x.3 Functional Requirements
     REQ-n: The system shall <requirement>.   [src: <internal-id>]
-->

## 5. Other Nonfunctional Requirements
### 5.1 Performance Requirements <!-- [REQUIREMENTS] NFR-n, nfr_category = performance -->
### 5.2 Safety Requirements <!-- [REQUIREMENTS] NFR-n, nfr_category = safety -->
### 5.3 Security Requirements <!-- [REQUIREMENTS] NFR-n, nfr_category = security -->
### 5.4 Software Quality Attributes <!-- [REQUIREMENTS] NFR-n: usability/reliability/maintainability/portability/compatibility -->
### 5.5 Business Rules <!-- [REQUIREMENTS] BR-n, rtype = business -->

## 6. Other Requirements <!-- [TBD] data retention, i18n, reuse, etc. -->

## Appendix A: Glossary
<!-- [TABLE] Term | Definition -->

## Appendix B: Analysis Models
<!-- [APPENDIX] Seed use-case / DFD / ERD produced from these requirements are DESIGN-phase
     artifacts; referenced here as placeholders and attached when available. -->

## Appendix C: To Be Determined List
<!-- [APPENDIX / TABLE] ID | Description — the open-questions / conflicts / TBD items -->
| ID | Description |
|---|---|
| TBD-1 | &lt;open item&gt; |
