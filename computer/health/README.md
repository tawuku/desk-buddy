# Apple Health -> JARVIS (iPhone Shortcut)

Your iPhone saves a tiny text file a few times a day to **iCloud Drive ->
Shortcuts -> JARVIS** (`health-YYYY-MM-DD.txt`). JARVIS reads it on this Mac:
no server, no open port, nothing leaves your own iCloud.

What it's used for:
- **Briefing**: a HEALTH card -- sleep vs. your goal, steps vs. your goal,
  7-day average and the trend on the week before.
- **Voice**: "how did I sleep?", "how many steps today?", "what's my resting
  heart rate?", "how active was I this week?"
- **Pet**: a walk nudge after 15:00 when you're under half your step goal; a
  gentle card in the morning after a short night. Goals and the on/off switch
  are in the pet's *Goals & reminders* window -> Reminders -> Apple Health,
  which also shows the last sync.

## Build the Shortcut (iPhone, ~5 minutes)

1. Shortcuts -> **+** -> name it `JARVIS Health`.
2. **Find Health Samples**: Type `Steps`, Start Date *is today* ->
   **Calculate Statistics** -> Sum -> **Round Number**. Rename the result `Steps`.
3. Same for `Active Energy` (-> `Kcal`) and `Exercise Minutes` (-> `Exercise`).
4. Sleep: **Find Health Samples**: `Sleep Analysis`, Start Date *is in the last
   1 day*, Value *is not* In Bed and *is not* Awake -> **Get Details of Health
   Sample** -> Duration -> **Calculate Statistics** -> Sum. Rename `Sleep`.
5. (Optional) `Resting Heart Rate`, latest first, limit 1 -> `HR`.
6. **Text** (Current Date with custom format `yyyy-MM-dd`):

       date: [Current Date]
       steps: [Steps]
       active_kcal: [Kcal]
       exercise_min: [Exercise]
       sleep: [Sleep]
       resting_hr: [HR]

7. **Save File**: Ask Where to Save *off*, destination `Shortcuts`, subpath
   `JARVIS/health-[Current Date].txt`, Overwrite If File Exists *on*.
8. Run it once, allow Health access.
9. Automation -> Time of Day -> **07:30**, daily, *Run Immediately* -> run
   `JARVIS Health`. Add **15:00** and **21:30** too.

Any field can be left out. Numbers may use German or English formatting and
units ("8.123", "451,6 kcal", "7 hr 5 min", seconds) -- the readers
(`computer/voice/health.py`, `computer/pet/health.js`) handle all of them.
