<!-- Source text for the User Training and Support proposal (.docx). -->
<!-- FRONT PAGE: Verdana 14, centred. Everything after it: Verdana 11. -->

# FRONT PAGE

User Training and Support for NACC SYS
Pre-Assessment and Case Management
and Support System

Training Dates: December 3–4, 2026
Venue: NACC RACCO I Conference Room

Proposed by:
Cacdac, Vincent Adrian M.
Carmelo, Levi Jedidiah N.
Lopez, Paul Benedict C.
Pasco, Paolo Antonio D.
Peñamora, John Reynold C.
Valmonte, Prex Cairo A.

# I. RATIONALE

NACC SYS is the Pre-Assessment and Case Management and Support System of NACC RACCO I. It runs entirely on an office computer: records are kept in a local SQLite database, uploaded files on the computer's own disk, and users open it in a web browser at http://localhost:5173. It needs no internet connection.

The system serves three kinds of users who do very different work. Social Workers (shown on screen as "SW (Staff)") keep their own children's records, file case referrals, ask a psychologist to take a child, book sessions, and view or download the psychological report once the psychologist uploads it. Psychologists accept or decline those requests, post their availability, run the pre-assessment, record remarks, treatment plans, results and reports, and close the clinical work. The Information Systems Administrator (shown as "ISA (Administrator)") approves new accounts, manages users, moves records between social workers, closes cases by their outcome and keeps the system healthy.

Each role sees different screens and follows rules the system enforces: a session cannot be booked until a case referral is on file, and a psychologist is assigned only once they accept. Users who have not been shown these rules lose time to refusals they do not understand. This proposal sets out a two-day, role-based, hands-on training program followed by a 30-day support period.

# II. OBJECTIVES

General Objective

To equip the social workers, psychologists and the ISA (Administrator) of NACC RACCO I with the knowledge and skills to use NACC SYS correctly and securely in their daily work, and to establish a support arrangement that keeps the system usable after the training.

Specific Objectives

By the end of the training, the participants will be able to:

1. Sign in, change a temporary password issued by the ISA, move around the system, find a child with the header search (Ctrl+K), and read notifications and the audit trail.
2. (Social Workers) Add a child's record through the record form, file a case referral, ask a psychologist to take the child, record the custodian's consent, start a QR self-report survey, and prepare and finalize the case study (SCSR).
3. (Psychologists) Accept or decline a case request, post availability and leave, complete the six-step pre-assessment, record remarks, treatment plans, result entries and reports, and close a case for a clinical reason.
4. (All) Book, move and cancel sessions on the calendar, and explain why the system refuses a booking.
5. (ISA) Review access requests, add users, reset passwords, transfer records, read the admin console, close a case by its outcome, and back up the database and uploaded files.
6. Use the assistant features within their limits and explain the data privacy rules the system follows.
7. Report a problem through the proper support channel.


# III. PROGRAM OF ACTIVITIES

[TABLE: Program of Activities — columns: Topic / System | Date / Time | Venue | Speaker | Audience]

| Topic / System | Date / Time | Venue | Speaker | Audience |
|---|---|---|---|---|
| Pre-training set-up of computers and demo data | Dec 2, 2026 (Wed), 1:00–5:00 PM | Conference Room | L. J. Carmelo | Training team |
| Registration and pre-test | Dec 3, 2026 (Thu), 8:00–8:30 AM | Conference Room | P. A. Pasco | All participants |
| Opening prayer, national anthem and welcome remarks | Dec 3, 8:30–8:45 AM | Conference Room | Head of Office | All participants |
| Session 1: Orientation to NACC SYS | Dec 3, 8:45–10:00 AM | Conference Room | J. R. Peñamora | All participants |
| Session 2: Child Records | Dec 3, 10:15 AM–12:00 NN | Conference Room | J. R. Peñamora | Social Workers, ISA |
| Session 3: Scheduling and the Calendar | Dec 3, 1:00–2:45 PM | Conference Room | V. A. Cacdac | All participants |
| Session 4: Case Study (SCSR) and QR self-report survey | Dec 3, 3:00–4:30 PM | Conference Room | J. R. Peñamora | Social Workers |
| Day 1 recap, questions and exit slip | Dec 3, 4:30–5:00 PM | Conference Room | J. R. Peñamora | All participants |
| Session 5: Clinical Workspace | Dec 4, 2026 (Fri), 8:30–10:30 AM | Conference Room | V. A. Cacdac | Psychologists |
| Session 6: The Assistant and Data Privacy | Dec 4, 10:45 AM–12:00 NN | Conference Room | J. R. Peñamora | All participants |
| Session 7A: System Administration | Dec 4, 1:00–2:30 PM | ISA Workstation Area | L. J. Carmelo | ISA |
| Session 7B: Integrated Case Simulation | Dec 4, 1:00–2:30 PM | Conference Room | J. R. Peñamora; V. A. Cacdac | Social Workers, Psychologists |
| Session 8: User Support | Dec 4, 2:45–3:45 PM | Conference Room | L. J. Carmelo | All participants |
| Post-test and training evaluation | Dec 4, 3:45–4:30 PM | Conference Room | P. A. Pasco | All participants |
| Awarding of certificates and closing remarks | Dec 4, 4:30–5:00 PM | Conference Room | Head of Office; J. R. Peñamora | All participants |

Session content

Session 1 — Orientation. What the system is for and what it never stores (test items, scales, scoring keys or scans of published instruments, which are given on paper). The three roles; signing in and the password change for accounts created or reset by the ISA; My Profile; the header search (Ctrl+K); notifications; the audit trail; and the confirmation asked before every save.

Session 2 — Child Records (SW). The four steps of the record form: Child's Profile (the category first, then a case type that pairs with it), Present Environment (address and present custodian), Recommendation, and Assignment. The duplicate check, filing the case referral, asking a psychologist (assigned only when they accept), the custodian's consent to texts, and the "Needs attention" column.

Session 3 — Scheduling. Posting weekly availability and recording leave. The booking calendar offers only days that can be booked: a session needs a case referral, cannot overlap another, and cannot fall on a day of leave. Moving and cancelling a session. Custodian texts are sent only when a number and consent are recorded; in the local copy they appear as lines in the API window.

Session 4 — Case Study and Survey (SW). Block A for every child, Blocks B and C for adoption cases; saving section by section; marking the case study Final, printing it for signing, and reopening it. Starting a QR self-report survey from the child's page.

Session 5 — Clinical Workspace (Psychologists). Accepting or declining a request (a decline needs a reason). The six-step pre-assessment (Child, Consent, Interview, Instruments, Problems, Complete) and "save and quit for now". Uploading an interview template (.docx or PDF). Remarks, treatment plans, result entries, reports (PDF or .docx), Monitoring, and closing a case with a clinical reason the record supports.

Session 6 — The Assistant and Data Privacy. The optional assistant runs on the office computer (Ollama): the pre-session brief for psychologists, "Polish writing", and the chat panel, which answers from the records instead of inventing them. Why a draft can be refused, how children's names are kept from the model, and each user's duties under the Data Privacy Act of 2012.

Session 7A — System Administration (ISA). The admin console; access requests; adding users; resetting a password; transferring a departing social worker's records; Agency details; the assistant switch; closing a case by its outcome; and backing up by copying backend\db.sqlite3 and the backend\media folder to a USB drive with the system's windows closed (there is no backup button).

Session 7B — Integrated Simulation (SW and Psychologists). In pairs at one computer, signing in alternately with the practice accounts of each role: the SW adds a fictional child and asks the psychologist; the psychologist accepts, books the first session, completes the pre-assessment and uploads a report; the SW finalizes the case study; the psychologist closes the case.

Session 8 — User Support. Describing a problem, the issue log form, the support channels, and common fixes: restarting run-local.bat, a forgotten password (the ISA resets it), a refused booking, and a record "missing" because it belongs to another social worker.

# IV. FLOW OF PROGRAM

[TABLE: Day 1 — Thursday, December 3, 2026 — columns: Time | Activity | Person in Charge | Method]

| Time | Activity | Person in Charge | Method |
|---|---|---|---|
| 8:00–8:30 AM | Registration; pre-test | P. A. Pasco | Written pre-test |
| 8:30–8:45 AM | Opening prayer, national anthem, welcome remarks | Head of Office | — |
| 8:45–10:00 AM | Session 1: Orientation | J. R. Peñamora | Lecture and live demonstration |
| 10:00–10:15 AM | Morning snack | P. C. Valmonte | — |
| 10:15 AM–12:00 NN | Session 2: Child Records | J. R. Peñamora | Demonstration, then guided hands-on |
| 12:00–1:00 PM | Lunch break | P. C. Valmonte | — |
| 1:00–2:45 PM | Session 3: Scheduling | V. A. Cacdac | Hands-on in SW–psychologist pairs |
| 2:45–3:00 PM | Afternoon snack | P. C. Valmonte | — |
| 3:00–4:30 PM | Session 4: Case Study and Survey | J. R. Peñamora | Hands-on with demo records |
| 4:30–5:00 PM | Day 1 recap; questions; exit slip | J. R. Peñamora | Open forum |

[TABLE: Day 2 — Friday, December 4, 2026 — columns: Time | Activity | Person in Charge | Method]

| Time | Activity | Person in Charge | Method |
|---|---|---|---|
| 8:00–8:30 AM | Registration; Day 1 review | P. A. Pasco | Recap quiz |
| 8:30–10:30 AM | Session 5: Clinical Workspace (Psychologists); review and practice of Day 1 topics (Social Workers and interns) | V. A. Cacdac; P. B. Lopez | Hands-on |
| 10:30–10:45 AM | Morning snack | P. C. Valmonte | — |
| 10:45 AM–12:00 NN | Session 6: The Assistant and Data Privacy | J. R. Peñamora | Demonstration; case discussion |
| 12:00–1:00 PM | Lunch break | P. C. Valmonte | — |
| 1:00–2:30 PM | Session 7A (ISA) and Session 7B (SWs and Psychologists), in parallel | L. J. Carmelo; J. R. Peñamora and V. A. Cacdac | Hands-on; return demonstration |
| 2:30–2:45 PM | Afternoon snack | P. C. Valmonte | — |
| 2:45–3:45 PM | Session 8: User Support | L. J. Carmelo | Lecture; troubleshooting drill |
| 3:45–4:30 PM | Post-test; training evaluation form | P. A. Pasco | Written test; survey |
| 4:30–5:00 PM | Awarding of certificates; closing remarks | Head of Office; J. R. Peñamora | — |

Training approach. Each training computer is a complete practice office: the demo data command fills it with 40 invented children, six months of history and practice accounts for each role. Participants use the practice account of their role; for exercises that pass a case between roles, a pair shares one computer and signs in alternately. No real child's record is used. Each session follows one cycle: demonstration, hands-on practice with a facilitator, and a short checked exercise.

# V. LOGISTICS

A. People

[TABLE: columns: Role | Number | Responsibility]

| Role | Number | Responsibility |
|---|---|---|
| Lead Trainer: Peñamora, John Reynold C. | 1 | Sessions 1, 2, 4, 6 and 7B; Day 1 recap; overall program |
| Co-Trainer: Cacdac, Vincent Adrian M. | 1 | Sessions 3, 5 and 7B |
| Technical Support Lead: Carmelo, Levi Jedidiah N. | 1 | Set-up of computers, Sessions 7A and 8, on-the-day troubleshooting |
| Facilitator: Lopez, Paul Benedict C. | 1 | Assisting during hands-on; Day 2 practice for social workers; return demonstration checklists |
| Training Secretariat: Pasco, Paolo Antonio D. | 1 | Registration, attendance, tests, evaluation forms, documentation |
| Logistics Coordinator: Valmonte, Prex Cairo A. | 1 | Venue, food and equipment, with the agency's staff |
| Head of Office, NACC RACCO I | 1 | Opening and closing remarks; observer |
| Participants: Social Workers | 13 (est.) | Training audience |
| Participants: Psychologists | 3 (est.) | Training audience |
| Participant: ISA | 1 | Training audience; first line of support afterwards |
| Participants: Social Work Interns | 3 | Training audience; attend the social workers' sessions |

Participant numbers are estimates, to be confirmed with the Head of Office a week before. There are 20 participants (13 social workers, 3 psychologists, 1 ISA and 3 social work interns); with the Head of Office and the 6 members of the development team, food is planned for 27 persons.

B. Infrastructure and Equipment

1. Venue: the NACC RACCO I Conference Room for all sessions; the ISA's workstation area for Session 7A. Both are inside the office, so no venue rental is needed.
2. Computers: 21 laptops or office PCs (one for each of the 20 participants and one for the trainer), plus 2 spares, each with Python 3.11+ and Node.js LTS, the local copy installed through setup-local.bat and the demo data loaded. They are the agency's computers and the training team's laptops.
3. Projector and screen, HDMI cable, a wireless presenter, and a speaker for the trainer's machine.
4. Power: extension cords for every table, and an uninterruptible power supply (UPS) for the trainer's computer.
5. Internet: not required. The local copy runs without it; Google sign-in and email are off in the local copy, and text messages are written to the API window instead of being sent.
6. The assistant: Ollama with the qwen2.5:3b-instruct model on the trainer's computer, for Session 6 only.

C. Training Materials

The user manual for each role (Social Worker, Psychologist, ISA), given to every participant as a PDF, with one printed copy per role kept in the office; a one-page quick reference card per role, printed on bond paper; exercise sheets and return demonstration checklists; the pre-test, post-test and evaluation form; the issue log form and support contact card; and certificates of completion.

D. Food

Morning and afternoon snacks and lunch for 27 persons (20 participants, the Head of Office and the 6 members of the development team) on both training days are provided by the agency at no cost to the program.

E. Financial Requirements

[TABLE: columns: Item | Computation | Amount (PHP)]

| Item | Computation | Amount (PHP) |
|---|---|---|
| Food (snacks and lunch) | provided by the agency | 0.00 |
| Printed user manuals (office copies) | 3 copies (one per role) × ₱100 | 300.00 |
| Quick reference cards (bond paper) | 20 × ₱5 | 100.00 |
| Certificates | 20 × ₱10 | 200.00 |
| Tests, exercise sheets and evaluation forms | printed in the office, lump sum | 150.00 |
| Transportation of the development team | 6 members × 2 days × ₱75 | 900.00 |
| Subtotal | | 1,650.00 |
| Contingency (10%) | | 165.00 |
| TOTAL | | 1,815.00 |

The venue, computers, projector, power and software cost nothing: they are the agency's own and the team's, the system needs no internet, and its software is free. The backup exercise uses the agency's existing USB drive. The amount above is shared by the development team.

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

1. Pre-test and post-test of 20 role-specific items, based on the functions and responsibilities of each user role (Social Workers, Psychologists and ISA), to measure the participants' knowledge before and after the training.
2. Return demonstration: each participant performs the core tasks of their role on the demo data and must complete at least 80% of the tasks on the role's return demonstration checklist.
3. Training evaluation form (5-point scale) on the content, speakers, materials, venue and food.
4. Success targets: at least 80% of participants score 80% or higher on the post-test; every participant reaches at least 80% in the return demonstration for their role; and the overall satisfaction rating on the evaluation form is at least 4.0 out of 5.

Prepared by:
Cacdac, Vincent Adrian M.
Carmelo, Levi Jedidiah N.
Lopez, Paul Benedict C.
Pasco, Paolo Antonio D.
Peñamora, John Reynold C.
Valmonte, Prex Cairo A.

Noted by:

______________________________
Head of Office, NACC RACCO I
