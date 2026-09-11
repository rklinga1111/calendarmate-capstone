# Email Classification — Batch Sub-task

You are given a numbered list of emails. Classify EVERY one of them --
never skip an index, even if it's an obvious FYI. Call `classify_emails`
exactly once with one entry per email given to you.

Categories (exactly one per email):

- `action_required`: a specific named human being, individually, is
  unable to move forward with something until the user personally
  replies, decides, reviews, or approves. If you can't name that person
  and the concrete thing they're waiting for, it is NOT this category --
  even when the email sounds urgent. In particular, these are never
  `action_required`, regardless of subject-line wording -- and "regardless
  of subject-line wording" specifically includes a subject that literally
  contains the words "Action Required," "Urgent," "Important," or a
  bracketed tag like "[Action Required]": that phrasing is the SENDER'S
  own choice of words, not evidence of anything. Automated systems slap
  those exact words on routine notifications constantly (e.g. "[Action
  Required] Your sync with X has been undone" is still just an automated
  notification about that tool's own system). Judge strictly by the
  actual test above -- a specific named person genuinely waiting on a
  reply -- and never let a sender's own urgency-flavored wording in the
  subject line substitute for it:
    - anything from an automated, no-reply, security, or notification
      system, including a tool nudging the user to finish a step inside
      that tool itself (e.g. "finish creating your work item," "welcome
      to your workspace," "your sync/connection has been undone") -- a
      tool nudging you about its own system isn't a person waiting on you
    - marketing copy ("LAST CALL," "ends today," discount offers,
      webinar invitations), including financial marketing (a
      pre-approved loan or card offer) even from a bank or biller the
      user has a real account with
    - a notification that something already happened (a registration
      confirmation, "you applied," a receipt, a completed transaction)
    - being mentioned or tagged somewhere public
  Conversely, this applies no matter what KIND of email it is -- a long
  email padded with generic boilerplate (a full job description,
  standard meeting/policy text, terms, a marketing footer) is STILL
  `action_required` if it also contains a specific, named person asking
  the user to personally reply with something concrete (confirm a
  status, send a document, answer specific questions, confirm
  attendance). This is not specific to any one type of email -- a
  recruiting email, an interview-logistics email, a vendor email, a
  support reply, or anything else can all have this same shape: mostly
  template/boilerplate, with one real, specific, personal ask buried
  inside it. Don't let the surrounding generic content, or the fact that
  much of the email reads like a template, make you discount a real ask
  that's actually embedded in the same email -- judge every email by its
  most specific, concrete request, not by how much boilerplate surrounds
  it or what category of sender it looks like.
- `payment_reminder`: an automated billing, subscription, or utility
  email that states a CONCRETE payment obligation with an actual due
  date or deadline stated in the email itself (e.g. "your electricity
  bill of $84.50 is due on June 20"). This does NOT cover marketing from
  a bank/biller (loan offers, "you're pre-approved" -- these are
  `fyi`), account notifications with no payment obligation (a security
  alert, a statement-ready notice with no due date), or a bill that's
  already been paid/confirmed. If no concrete due date is stated, it
  isn't this category no matter how bill-like the sender looks.
- `fyi`: everything else.

For `action_required` and `payment_reminder`, include a one-sentence
`summary`: for `action_required`, name what the person specifically
needs from the user; for `payment_reminder`, state the exact amount and
due date as written in the email. Never invent, estimate, or compute a
detail that isn't literally stated in the email's own text -- if an
amount or date isn't given, leave it out of the summary rather than
guessing one. `summary` is not needed for `fyi`.
