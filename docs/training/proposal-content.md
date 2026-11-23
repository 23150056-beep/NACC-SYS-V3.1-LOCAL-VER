<!-- Source text for the User Training and Support proposal (.docx). -->
<!-- FRONT PAGE: Verdana 14, centred. Everything after it: Verdana 11. -->

# FRONT PAGE

NATIONAL AUTHORITY FOR CHILD CARE
Regional Alternative Child Care Office I (RACCO I)

FINAL PROPOSAL FOR USER TRAINING AND SUPPORT

User Training and Support for NACC SYS V3.1 (Local Version):
Child Case Management and Counseling Support System

Training Dates: December 3–4, 2026
Venue: NACC RACCO I Conference Room

Submitted by:
Reynold Canedo
Proponent and System Developer

Date Submitted: November 23, 2026

# I. RATIONALE

NACC SYS V3.1 (Local Version) is the web-based Child Case Management and Counseling Support System of NACC RACCO I. It runs entirely on an office computer: records are kept in a local SQLite database, uploaded files on the computer's own disk, and users open it in a web browser at http://localhost:5173. It needs no internet connection.

The system serves three kinds of users who do very different work. Social Workers (shown on screen as "SW (Staff)") keep their own children's records, file case referrals, ask a psychologist to take a child, book sessions and prepare the Social Case Study Report (SCSR). Psychologists accept or decline those requests, post their availability, run the pre-assessment, record remarks, treatment plans, results and reports, and close the clinical work. The Information Systems Administrator (shown as "ISA (Administrator)") approves new accounts, manages users, moves records between social workers, closes cases by their outcome and keeps the system healthy.

Each role sees different screens and follows rules the system enforces: a session cannot be booked until a case referral is on file, and a psychologist is assigned only once they accept. Users who have not been shown these rules lose time to refusals they do not understand. This proposal sets out a two-day, role-based, hands-on training program followed by a 30-day support period.

# II. OBJECTIVES

General Objective

To equip the social workers, psychologists and the ISA of NACC RACCO I with the knowledge and skills to use NACC SYS V3.1 (Local Version) correctly and securely in their daily work, and to establish a support arrangement that keeps the system usable after the training.

Specific Objectives

By the end of the training, the participants will be able to:

1. Sign in, change a temporary password issued by the ISA, move around the system, find a child with the header search (Ctrl+K), and read notifications and the audit trail.
2. (Social Workers) Add a child's record through the record form, file a case referral, ask a psychologist to take the child, record the custodian's consent, start a QR self-report survey, and prepare and finalize the case study (SCSR).
3. (Psychologists) Accept or decline a case request, post availability and leave, complete the six-step pre-assessment, record remarks, treatment plans, result entries and reports, and close a case for a clinical reason.
4. (All) Book, move and cancel sessions on the calendar, and explain why the system refuses a booking.
5. (ISA) Review access requests, add users, reset passwords, transfer records, read the admin console, close a case by its outcome, and back up the database and uploaded files.
6. Use the assistant features within their limits and explain the data privacy rules the system follows.
7. Report a problem through the proper support channel.

Success targets: at least 80% of participants score 80% or higher on the post-test, every participant passes the return demonstration for their role, and the overall satisfaction rating is at least 4.0 out of 5.

# III. PROGRAM OF ACTIVITIES

[TABLE: Program of Activities — columns: Topic / System | Date / Time | Venue | Speaker | Audience]

| Topic / System | Date / Time | Venue | Speaker | Audience |
|---|---|---|---|---|
| Pre-training set-up of computers and demo data | Dec 2, 2026 (Wed), 1:00–5:00 PM | Conference Room | Technical Support Lead | Training team |
| Session 1: Orientation to NACC SYS V3.1 | Dec 3, 2026 (Thu), 8:45–10:00 AM | Conference Room | R. Canedo, Lead Trainer | All participants |
| Session 2: Child Records | Dec 3, 10:15 AM–12:00 NN | Conference Room | R. Canedo, Lead Trainer | Social Workers, ISA |
| Session 3: Scheduling and the Calendar | Dec 3, 1:00–2:45 PM | Conference Room | Co-Trainer | All participants |
| Session 4: Case Study (SCSR) and QR self-report survey | Dec 3, 3:00–4:30 PM | Conference Room | R. Canedo, Lead Trainer | Social Workers |
| Session 5: Clinical Workspace | Dec 4, 2026 (Fri), 8:30–10:30 AM | Conference Room | Co-Trainer | Psychologists |
| Session 6: The Assistant and Data Privacy | Dec 4, 10:45 AM–12:00 NN | Conference Room | R. Canedo, Lead Trainer | All participants |
| Session 7A: System Administration | Dec 4, 1:00–2:30 PM | ISA Workstation Area | Technical Support Lead | ISA |
| Session 7B: Integrated Case Simulation | Dec 4, 1:00–2:30 PM | Conference Room | Lead and Co-Trainer | Social Workers, Psychologists |
| Session 8: User Support | Dec 4, 2:45–3:45 PM | Conference Room | Technical Support Lead | All participants |
| Post-test, evaluation and closing program | Dec 4, 3:45–5:00 PM | Conference Room | Training team | All participants |
| Post-training support | Dec 7, 2026 – Jan 8, 2027, 8:00 AM–5:00 PM | Office; phone or chat | ISA; development team | All users |

Session content

Session 1 — Orientation. What the system is for and what it never stores (test items, scales, scoring keys or scans of published instruments, which are given on paper). The three roles; signing in and the password change asked of accounts created or reset by the ISA; My Profile; the header search (Ctrl+K) and its quick actions; the notification bell; the audit trail; and the "Are you sure you want to proceed?" confirmation asked before every save.

Session 2 — Child Records (SW). The four steps of the record form: Child's Profile (the category first, then a case type that pairs with it), Present Environment (address and present custodian), Recommendation, and Assignment. Required answers, the duplicate check, filing the case referral, asking a psychologist (the child is assigned only when they accept), recording the custodian's consent to texts, and the "Needs attention" column on Records.

Session 3 — Scheduling. Posting weekly availability and recording leave. The booking calendar offers only days that can be booked: a session needs a case referral, cannot overlap another, and cannot fall on a day of leave. Moving and cancelling a session. Custodian texts are sent only when a number and consent are recorded; in the local copy they appear as lines in the API window.

Session 4 — Case Study and Survey (SW). Block A for every child, Blocks B and C for adoption cases; saving section by section; marking the case study Final, printing it for signing, and reopening it. Starting a QR self-report survey from the child's page.

Session 5 — Clinical Workspace (Psychologists). Accepting or declining a request (a decline needs a reason). The six-step pre-assessment (Child, Consent, Interview, Instruments, Problems, Complete) and "save and quit for now". Uploading an interview template (.docx or PDF). Remarks, treatment plans, result entries, reports (PDF or .docx) and the check before filing, the printed report, Monitoring, and closing a case with a clinical reason the record supports.

Session 6 — The Assistant and Data Privacy. The optional assistant runs on the office computer (Ollama): the pre-session brief for psychologists, "Polish writing", and the chat panel, which answers from the records instead of inventing them. Why a draft can be refused, how children's names are kept from the model, and each user's duties under the Data Privacy Act of 2012.

Session 7A — System Administration (ISA). The admin console's queues and health rows; access requests (a requested role is a claim, never a grant); adding users; resetting a password; transferring a departing social worker's records; Agency details; the assistant switch; closing a case by its outcome; and backing up by copying backend\db.sqlite3 and the backend\media folder to a USB drive with the system's windows closed (there is no backup button).

Session 7B — Integrated Simulation (SW and Psychologists). In pairs at one computer, signing in alternately with the practice accounts of each role: the SW adds a fictional child and asks the psychologist; the psychologist accepts, books the first session, completes the pre-assessment and uploads a report; the SW finalizes the case study; the psychologist closes the case.

Session 8 — User Support. Describing a problem (screen, time, what was pressed, the exact message), the issue log form, the support channels, and common fixes: restarting run-local.bat, a forgotten password (the ISA resets it), a refused booking, and a record "missing" because it belongs to another social worker.

# IV. FLOW OF PROGRAM

[TABLE: Day 1 — Thursday, December 3, 2026 — columns: Time | Activity | Person in Charge | Method]

| Time | Activity | Person in Charge | Method |
|---|---|---|---|
| 8:00–8:30 AM | Registration; pre-test | Training Secretariat | Written pre-test |
| 8:30–8:45 AM | Opening prayer, national anthem, welcome remarks | Head of Office | — |
| 8:45–10:00 AM | Session 1: Orientation | Lead Trainer | Lecture and live demonstration |
| 10:00–10:15 AM | Morning snack | Logistics Officer | — |
| 10:15 AM–12:00 NN | Session 2: Child Records | Lead Trainer | Demonstration, then guided hands-on |
| 12:00–1:00 PM | Lunch break | Logistics Officer | — |
| 1:00–2:45 PM | Session 3: Scheduling | Co-Trainer | Hands-on in SW–psychologist pairs |
| 2:45–3:00 PM | Afternoon snack | Logistics Officer | — |
| 3:00–4:30 PM | Session 4: Case Study and Survey | Lead Trainer | Hands-on with demo records |
| 4:30–5:00 PM | Day 1 recap; questions; exit slip | Lead Trainer | Open forum |

[TABLE: Day 2 — Friday, December 4, 2026 — columns: Time | Activity | Person in Charge | Method]

| Time | Activity | Person in Charge | Method |
|---|---|---|---|
| 8:00–8:30 AM | Registration; Day 1 review | Training Secretariat | Recap quiz |
| 8:30–10:30 AM | Session 5: Clinical Workspace (SWs: practice clinic on Day 1 topics) | Co-Trainer; Facilitator | Hands-on |
| 10:30–10:45 AM | Morning snack | Logistics Officer | — |
| 10:45 AM–12:00 NN | Session 6: The Assistant and Data Privacy | Lead Trainer | Demonstration; case discussion |
| 12:00–1:00 PM | Lunch break | Logistics Officer | — |
| 1:00–2:30 PM | Session 7A (ISA) and Session 7B (SWs and Psychologists), in parallel | Technical Support Lead; Lead Trainer and Co-Trainer | Hands-on; return demonstration |
| 2:30–2:45 PM | Afternoon snack | Logistics Officer | — |
| 2:45–3:45 PM | Session 8: User Support | Technical Support Lead | Lecture; troubleshooting drill |
| 3:45–4:30 PM | Post-test; training evaluation form | Training Secretariat | Written test; survey |
| 4:30–5:00 PM | Awarding of certificates; closing remarks | Head of Office; Lead Trainer | — |

Training approach. The local copy keeps each computer's records to itself, so every training computer is a complete practice office: the system's demo data command fills it with 40 invented children and six months of history, and with practice accounts for social workers and psychologists. Participants work on their own computer with the practice account of their role; for exercises that pass a case between roles, a pair shares one computer and signs in alternately. No real child's record is used. Each session follows one cycle: the trainer demonstrates, participants repeat the task with a facilitator walking the room, and a short exercise is checked.

# V. LOGISTICS

A. People

[TABLE: columns: Role | Number | Responsibility]

| Role | Number | Responsibility |
|---|---|---|
| Lead Trainer (Reynold Canedo, System Developer) | 1 | Sessions 1, 2, 4, 6 and 7B; overall program |
| Co-Trainer | 1 | Sessions 3, 5 and 7B |
| Technical Support Lead | 1 | Set-up of computers, Sessions 7A and 8, on-the-day troubleshooting |
| Facilitator / Training Secretariat | 1 | Attendance, tests, evaluation forms, assisting during hands-on |
| Logistics Officer (from the agency) | 1 | Venue, food, equipment |
| Head of Office, NACC RACCO I | 1 | Opening and closing remarks; observer |
| Participants: Social Workers | 7 (est.) | Training audience |
| Participants: Psychologists | 3 (est.) | Training audience |
| Participant: ISA | 1 | Training audience; first line of support afterwards |

Participant numbers are estimates, to be confirmed with the Head of Office a week before. Food and materials are computed for 17 persons (11 participants, the Head of Office and 5 members of the training team).

B. Infrastructure and Equipment

1. Venue: the NACC RACCO I Conference Room for all sessions; the ISA's workstation area for Session 7A. Both are inside the office, so no venue rental is needed.
2. Computers: 12 laptops or office PCs (one per participant and one for the trainer), plus 2 spares, each with Python 3.11+ and Node.js LTS, the local copy installed through setup-local.bat and the demo data loaded. They are the agency's computers and the training team's laptops.
3. Projector and screen, HDMI cable, a wireless presenter, and a speaker for the trainer's machine.
4. Power: extension cords for every table, and an uninterruptible power supply (UPS) for the trainer's computer.
5. Internet: not required. The local copy runs without it; Google sign-in and email are off in the local copy, and text messages are written to the API window instead of being sent.
6. The assistant: Ollama with the qwen2.5:3b-instruct model on the trainer's computer, for Session 6 only.

C. Training Materials

A printed, ring-bound user manual for each role (Social Worker, Psychologist, ISA); a laminated one-page quick reference card per role; exercise sheets and return demonstration checklists; the pre-test, post-test and evaluation form; the issue log form and support contact card; and certificates of completion.

D. Food

Morning and afternoon snacks and lunch for 17 persons on both training days, coordinated by the Logistics Officer with a local caterer.

E. Financial Requirements

[TABLE: columns: Item | Computation | Amount (PHP)]

| Item | Computation | Amount (PHP) |
|---|---|---|
| Lunch | 17 persons × 2 days × ₱200 | 6,800.00 |
| AM and PM snacks | 17 persons × 2 days × 2 × ₱75 | 5,100.00 |
| User manuals (printed, ring-bound) | 17 copies × ₱150 | 2,550.00 |
| Quick reference cards (laminated) | 17 × ₱30 | 510.00 |
| Training kits (notepad, pen, ID) | 17 × ₱60 | 1,020.00 |
| Certificates | 17 × ₱20 | 340.00 |
| Tests and evaluation forms | lump sum | 300.00 |
| Tarpaulin | 1 × ₱450 | 450.00 |
| USB flash drives for the backup exercise | 2 × ₱350 | 700.00 |
| Transportation of the training team and equipment | lump sum | 1,500.00 |
| Subtotal | | 19,270.00 |
| Contingency (10%) | | 1,927.00 |
| TOTAL | | 21,197.00 |

Computers, projector, venue and power are provided in kind at no cost. Proposed source of funds: the agency's training and development allocation, subject to the Head of Office's approval.

F. Contingency Plan

1. Power interruption: laptops run on battery and the trainer's computer is on a UPS.
2. A computer fails: one of the two prepared spares replaces it.
3. The assistant does not start: Session 6 uses prepared screenshots, and the rest of the program is unaffected.
4. A participant is absent: a make-up session is scheduled during the support period using the same materials.

# VI. USER SUPPORT PLAN

After the training, support is given in two lines for 30 days (December 7, 2026 to January 8, 2027, on working days).

[TABLE: columns: Level | Who | Handles | Target Response]

| Level | Who | Handles | Target Response |
|---|---|---|---|
| First line | ISA, inside the office | Password resets, account approvals, record transfers, "how do I" questions, restarting the local copy | Same working day |
| Second line | Development team, by phone, chat or email | Errors on screen, wrong results, data problems, anything the ISA cannot solve | Urgent (system cannot be used): within 4 working hours; others: within 1 working day |
| Improvement requests | Development team, with the Head of Office | Suggested changes, new features | Reviewed every two weeks |

Every problem is written on the issue log form (date, user, screen, what was done, the exact message) so it can be reproduced and fixed. A one-hour refresher in the second week of January 2027 covers the topics the log shows were hard, and the quick reference cards are updated from it.

# VII. EVALUATION

1. Pre-test and post-test of 20 items on the tasks of each role, to measure what was learned.
2. Return demonstration: each participant performs their role's core tasks on the demo data, checked by a facilitator against the role checklist.
3. Training evaluation form (5-point scale) on content, speakers, materials, venue and food; and, after 30 days, the number and kind of requests in the issue log.
The results are summarized in a post-training report submitted to the Head of Office within one week after the training.

Prepared by:

Reynold Canedo
Proponent and System Developer

Noted by:

______________________________
Head of Office, NACC RACCO I
