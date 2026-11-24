# Original requirements documents

This folder holds copies of the five original FYP requirements documents and the original
backend architecture plan. They record what was first specified. Approved changes are in the
clarifications document below.

The living versions are in the project owner's `FYP_Documentation` folder. Since 2026-10-27
those carry marked "implementation update" notes describing the software as built. The
copies here stay exactly as first received.

| File | What it covers |
|---|---|
| `AI_CCTV_FYP_Final_Proposal_and_Requirements.md` | Proposal and master requirements (Sections 1–23, 14A–14C) |
| `FYP_Final_Super_Admin_Dashboard_Pages_and_Their_Each_Page_Features.txt` | Platform Super Admin web app pages |
| `FYP_Final_Organization_Admin_Dashboard_Pages_and_Their_Each_Page_Features.txt` | Organization Admin web app pages |
| `FYP_Final_Supervisor_Dashboard_Each_Page_and_Their_Each_Page_Features.txt` | Supervisor web app pages |
| `FYP_Final_Guards_Mobile_App_Pages_and_Their_Each_Page_Features.txt` | Guard mobile app screens |
| `FYP_Final_Backend_Architecture.txt` | Original backend architecture plan (copied 2026-10-27 from `FYP_Documentation`, before that folder was updated) |

## Rules for this folder

- **Do not edit these six files.** Approved changes and clarifications go in a separate
  document. The first one is
  [`Approved_Clarifications_and_Decisions_Organization_Admin_Auth.md`](Approved_Clarifications_and_Decisions_Organization_Admin_Auth.md).
- Where an approved clarification differs from an original document, the clarification wins
  for the topic it covers. The clarifications document always says which original section it
  changes.

## Where these copies came from

The project owner attached the five documents in the Claude Code conversation of 2026-10-27.
Each file was saved with the exact text the conversation received, and checked against it
character by character. The text was extracted by the chat client, so if your own originals
are in another format (for example Word or PDF), those files remain the true originals.
Replace these copies with them if exact files matter.

SHA-256 checksums of the copies as saved:

```
25ebcf4932850e50be77afcf7047263b087c1581064a6028f5d2793cc7642b9a  AI_CCTV_FYP_Final_Proposal_and_Requirements.md
46ae37f2aad7d6e17828e390d311b05849d335fe124cede554c8b0db3ee9146c  FYP_Final_Guards_Mobile_App_Pages_and_Their_Each_Page_Features.txt
4206029bf6b58b8f84b8b3b584b4318bf22265bc3c11041df7687eddfdd04b7e  FYP_Final_Organization_Admin_Dashboard_Pages_and_Their_Each_Page_Features.txt
8f4419b11bfebe0b721a2720aad5d37af418df53b07cad735a2adef1cf8aa796  FYP_Final_Super_Admin_Dashboard_Pages_and_Their_Each_Page_Features.txt
8ad25a9badb3a7a8944d9f5f55e05f3266e2e8d8e55dbc0136205c9d4d524ac1  FYP_Final_Supervisor_Dashboard_Each_Page_and_Their_Each_Page_Features.txt
247487168dae974349420bb9e7bdf581cd4b795424ab45acb315d29119dff7a8  FYP_Final_Backend_Architecture.txt
```

Check them with `sha256sum -c` after pasting the block above into a file.
