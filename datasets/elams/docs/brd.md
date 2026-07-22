# ELAMS — Business Requirements Document

| Field | Value |
|---|---|
| Document title | Employee Leave & Attendance Management System (ELAMS) — Business Requirements Document |
| Version | 1.0 (Draft) |
| Status | For review |
| Owner | People Systems / HR Technology |
| Last updated | 2026-07-08 |

---

## 1. Purpose

The Employee Leave & Attendance Management System (ELAMS) is being introduced to replace the current mix of spreadsheets, email approvals, and manual balance tracking that the People Operations team maintains today. Leave data is currently reconciled by hand at the end of each month, which is slow, error-prone, and gives employees little visibility into their own balances. The purpose of this document is to capture the business needs for a single, authoritative system that lets employees request leave, lets managers approve it, and keeps leave balances accurate and auditable across the organization.

## 2. Scope

ELAMS covers the end-to-end leave lifecycle for full-time employees: requesting leave, manager approval, balance tracking, notifications, and reporting for HR administrators. The system will serve employees, their direct managers, and the HR-admin group, and is expected to be used from both desktop and mobile browsers. Payroll processing, timesheet billing, and public-holiday calendar administration are out of scope for this release and will continue to be handled by their existing owners; ELAMS will consume, but not manage, those data sources where needed.

## 3. Functional Requirements

The requirements below describe the behavior expected of ELAMS. Each subsection groups related capabilities in the order an employee typically encounters them, from submitting a request through to being notified of the outcome.

### 3.1 Leave Request

Employees need a simple, guided way to ask for time off without having to know the details of company leave policy up front. The system shall allow an employee to submit a leave request specifying leave type, start date, and end date. The request form is the primary entry point into the leave lifecycle, typically presenting only the leave types for which the employee is eligible.

### 3.2 Approval

Every request needs a clear, single point of accountability so that no request is lost in a shared inbox. The system shall route each submitted leave request to the employee's direct manager for approval. Managers will see pending requests in a dedicated queue, ordered so that the earliest start dates are easiest to act on first.

### 3.3 Balances

Accurate balances are the foundation of employee trust in the system, and preventing employees from over-drawing their entitlements is one of the core goals of this release. An employee may not request more leave days than their available balance for the selected leave type. Balances are tracked per leave category rather than as a single pooled figure. Each employee has a separate balance for casual, sick, and earned leave.

### 3.4 Deduction

Employees have raised concerns in the past about days being counted against them before a request is even reviewed, which is why the timing of any balance change is spelled out explicitly below. When a leave request is approved, the system shall deduct the leave days from the employee's balance. The deduction shall occur only after final approval, not on submission.

### 3.5 Notice Period

Adequate notice helps teams plan around absences while still accommodating genuine emergencies such as illness. Leave requests must be submitted at least 3 working days before the start date, except for sick leave. The intent is to give managers reasonable planning time without penalizing employees who fall ill unexpectedly.

### 3.6 Notifications

Timely communication of outcomes reduces the follow-up emails that People Operations currently fields every day. The system shall send an email notification to the employee when their leave request is approved or rejected. In practice these notifications are worded to make the decision clear and, where relevant, to reference the dates and leave type involved.

## 4. Business Rules

This section captures the policy-driven rules that govern how entitlements are calculated, independent of any particular screen or workflow. Earned leave shall accrue at 1.5 days per completed month of service. Accrual is based on completed months of continuous service, and the resulting balance is what feeds the availability checks described in Section 3.3.

## 5. Assumptions & Dependencies

This section records the working assumptions and external dependencies the project is relying on; these are contextual notes rather than testable requirements. It is assumed that authoritative employee records — including reporting lines used to determine each employee's direct manager — are owned by the existing HRMS and are reasonably up to date. It is further assumed that a corporate email service and a single-sign-on identity provider are already available for the organization to build upon. The public-holiday and working-day calendar is expected to be provided by the existing HR calendar source and treated as a reference input. Any change to these upstream systems may affect ELAMS behavior and is best reviewed jointly with their respective owners.

## 6. Non-Functional Requirements

These requirements describe qualities of the system — performance, security, accessibility, and record-keeping — as opposed to specific features. The leave dashboard should load quickly for all users. The system shall require multi-factor authentication for all manager and HR-admin logins. The system shall be accessible on mobile browsers (Chrome and Safari) at 375px width. The system shall retain an audit log of every leave approval and rejection for at least 7 years.

## 7. Constraints & Integrations

ELAMS operates within the organization's existing technology and compliance landscape rather than as a standalone island, and that landscape places firm constraints on where data lives and how the system exchanges information. All employee and leave data shall be stored within the India (Asia-South) region. The system shall integrate with the existing HRMS via its REST API for employee master data. Reusing the HRMS as the source of truth for employee master data avoids duplicate data entry and keeps reporting lines consistent across systems.
