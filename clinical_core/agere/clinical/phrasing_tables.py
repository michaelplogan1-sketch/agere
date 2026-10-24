"""Data for phrasing.py: how people word a healthcare-data question, mapped onto the wording the grammar already reads.

Everything here is a TABLE (a synonym, an abbreviation, an alternative construction), never a rule for one question. phrasing.py applies
the tables in order to a question and returns a reworded candidate; the planner tries it only after the question as typed was declined
and keeps it when the grammar reads it completely. A rewording never drops or adds a number, a year, a negation or a patient id
(phrasing.safe checks that), so the literal check and the decision context still see the same question.

WORDS      [(pattern, replacement)]    whole-word, case-insensitive substitutions (abbreviations, synonyms).
FRAMES     [(name, pattern, template)] alternative constructions: the matched span is replaced by the template ({group} = a named group).
           Tokens in a pattern: <DRUG> (a drug class / group / everyday name), <LAB> (a lab name), <COND> (a condition name).
           A template starting with '@' is computed in phrasing._special (it depends on the matched words).
"""

# -- words ------------------------------------------------------------------------------------------------------------------------
# (pattern, replacement). Patterns are matched case-insensitively between word boundaries.
WORDS = [
    # people
    (r'pts', 'patients'), (r'pt', 'patient'), (r"pt's", "patient's"),
    (r'members?\s+(?=with|who|whose|on|aged|over|under)', 'patients '),  # "members with diabetes" reads as patients with diabetes
    (r'asthmatics?', 'asthma patients'), (r'hypertensives', 'hypertensive patients'),
    # conditions
    (r'HTN', 'hypertension'), (r'a[- ]?fib', 'atrial fibrillation'), (r'T2DM|T2D', 'type 2 diabetes'), (r'T1DM|T1D', 'type 1 diabetes'),
    (r'CHF', 'heart failure'), (r'CAD', 'coronary artery disease'),
    (r'(?:hypertension|blood\s+pressure|bp)\s+control\s+rate', 'share of patients with hypertension who have blood pressure under control'),
    # labs and tests
    (r'hb\s?a1c|hgba1c|glyc(?:ated|osylated)\s+h(?:a?emo)?globin|glycohemoglobin|hemoglobin\s+a1c|a1c\s+(?:level|value|result|test)s?', 'A1c'),
    (r'urine\s+albumin\s*[-/]?\s*creatinine(?:\s+(?:ratio|test|tests|result|results))?|urine\s+acr|uacr|u-acr|microalbumin(?:\s+(?:test|tests))?',
     'urine albumin-creatinine ratio'),
    (r'ldl[- ]c', 'LDL'), (r'ldl\s+cholesterol', 'LDL'),
    (r'bp', 'blood pressure'), (r'sbp', 'systolic'), (r'dbp', 'diastolic'),
    (r'mammogram(?:s)?\s+screenings?|mammography\s+screenings?|screening\s+mammograms?|screening\s+mammography', 'mammogram'),
    (r'awv', 'annual wellness visit'), (r'awvs', 'annual wellness visits'),
    (r'seen\s+in\s+the\s+office', 'seen in an office visit'),
    (r'within\s+the\s+(last|past)', r'in the \1'),
    (r'zero', 'no'),
    (r'discontinued', 'stopped'), (r'discontinue', 'stop'), (r'discontinuing', 'stopping'), (r'discontinues', 'stops'), (r'discontinuation', 'stopping'),
    (r'brought\s+up', 'raised'),
    (r'(?:difficulty|trouble|problems?)\s+(?:with\s+)?(?:getting|get|making\s+it)\s+to\s+(?:their\s+)?appointments', 'trouble getting to appointments'),
    (r'struggles?\s+to\s+get\s+to\s+(?:their\s+)?appointments', 'has trouble getting to appointments'),
    (r'concern\s+about\s+(?:their\s+)?memory', 'memory concern'),
    (r'cpr', 'chest compressions'), (r'intubation|intubated', 'a breathing tube'),
    (r'drug\s+intolerances', 'drug intolerance'),
    (r'(?:the\s+)?(?:subsequent|following)\s+notes|(?:the\s+)?notes\s+(?:that\s+)?follow', 'later notes'),
    (r'continue\s+to\s+(list|show)', r'still \1'),
    (r'(?:the\s+)?one\s+(?:prior|previous|preceding)\s+to\s+(?:that|it|this)', 'the one before that'),
    (r'(?:the\s+)?(?:last|latest|most\s+recent)\s+of\s+(?:those|them|these)', 'the most recent one'),
    (r'(?:never|didn\'t|did\s+not)\s+(?:got|get|been)\s+filled', 'never filled'),
    (r'pay\s+out', 'pay'),
    (r'claims?\s+payments?', 'claims paid'),
    (r'(?:come|came)\s+back\s+to\s+the\s+hospital\s+within\s+(\d+)\s+days\s+(?:after|of)\s+(?:being\s+)?(?:a\s+)?discharge[d]?', r'have a readmission within \1 days of a discharge'),
    (r'what\s+did\s+we\s+pay\s+on\s+average\s+(?:for|on|per)', 'what is the average paid on'),
    # medicines
    (r'acei', 'ACE inhibitor'), (r'ace[- ]i', 'ACE inhibitor'), (r'ace[- ]inhibitors', 'ACE inhibitors'),
    (r'ics', 'inhaled corticosteroid'), (r'inhaled\s+(?:steroids?|corticosteroids?)', 'inhaled corticosteroid'),
    (r'doacs?', 'anticoagulant'), (r'blood\s+thinners?', 'anticoagulant'), (r'anticoagulation', 'anticoagulant'),
    (r'scripts', 'prescriptions'), (r'script', 'prescription'), (r'rx', 'prescription'), (r'rxs', 'prescriptions'),
    (r'meds', 'medications'), (r'med', 'medication'),
    (r'guideline[- ]directed\s+(?:medical\s+)?therapy', 'GDMT'),
    # utilization and money
    (r'emergency\s+(?:department|room)', 'ED'), (r'er', 'ED'),
    (r'utili[sz]ers', 'users'),
    (r'hospitali[sz]ations?', 'hospital admissions'),
    (r'no\s+shows?|no-shows?|missed\s+appointments?|missed\s+visits?', 'no-shows'),
    (r'p\.?m\.?p\.?m\.?|per\s+member\s+per\s+month', 'PMPM'),
    (r'(?:split|broken\s+down|grouped|segmented|sliced|broken\s+out|cut)\s+(?:up\s+)?by', 'by'),
    (r'spending', 'spend'), (r'generics', 'generic'), (r'mean', 'average'), (r'worst[- ]performing', 'worst'),
    (r'(?:over|during|within|throughout)\s+the\s+(last|past)\s+(year|12\s+months|twelve\s+months)', r'in the \1 \2'),
    (r'(?:rise|rose|increase|increased|go\s+up|went\s+up|going\s+up)\s+or\s+(?:fall|fell|decrease|decreased|decline|declined|go\s+down|went\s+down|going\s+down|drop|dropped)', 'go up or down'),
    (r'(?:increase|rise|growth)\s+or\s+(?:decrease|fall|decline|drop)', 'up or down'),
    (r'which\s+(?:drugs|medications|medicines)\s+(?:drove|contributed|moved|changed|caused)(?:\s+(?:the\s+change|it))?(?:\s+(?:the\s+)?most)?', 'which drugs moved it most'),
    (r'ED\s+(?:visit\s+)?rate\s+(?:for|relative\s+to)\s+(?:their\s+)?size', 'ED use relative to size'),
    (r'share\s+of\s+(?:their\s+)?patients\s+(?:going|who\s+go|that\s+go)\s+to\s+the\s+ED', 'patients to the ED relative to size'),
    (r'ED\s+visit\s+rate', 'ED visits'),
    (r'pdc\s+(?:of\s+)?(0?\.\d+)\s+or\s+(?:higher|more|above|greater)', r'PDC at least \1'),
    (r'patients\s+with\s+an?\s+recent\s+discharge', 'recently discharged patients'), (r'right\s+away', 'right now'),
    (r'\(\s*(\d+)\s*(?:and\s+(?:up|above|higher|over)|or\s+(?:more|higher|above))\s*\)', r'(\1+)'),
    (r'between\s+(20\d\d)\s+and\s+(20\d\d)', r'from \1 to \2'),
    (r'(?:the\s+)?(?:highest|largest|greatest|biggest)\s+(?:number|count)\s+of', 'the most'),
    (r'(?:the\s+)?(?:lowest|smallest)\s+(?:number|count)\s+of', 'the fewest'),
    (r'and\s+how\s+many\s+(?:did|do|does|were|are)?\s*(?:each|they|those|these)(?:\s+(?:have|had|of them))?', 'and how many'),
    (r'lob', 'line of business'),
    (r'per\s+cent', 'percent'),
    (r'high\s+cost', 'high-cost'),
    (r'ma\s+(?:book|business|plan)', 'Medicare Advantage'),
    (r'dual[- ]eligibles?|duals', 'dual-eligible'),
    (r'out[- ]of[- ]network', 'out of network'),
    (r'(?:was|were|is|are)\s+paid\s+out\s+of\s+network', 'went out of network'),
    (r'(?:that\s+)?went\s+back\s+to\s+stock', 'were returned to stock'),
    (r'(?:who\s+)?(?:are|is)\s+(?:located\s+)?in\s+(low[- ]food[- ]access\s+(?:areas?|neighborhoods?|tracts?))', r'live in \1'),
    (r'inpatient\s+admissions?', 'hospital admissions'),
]

# -- constructions -------------------------------------------------------------------------------------------------------------------
TIME = (r'(?:in|during|for|over|within|across|throughout)\s+(?:the\s+)?'
        r'(?:(?:last|past|previous|prior)\s+(?:\d+|one|two|three|four|five|six|twelve|eighteen)\s+(?:day|week|month|year|calendar year)s?'
        r'|(?:last|past|previous|prior)\s+(?:year|season|quarter|month)|20\d\d|(?:last|this)\s+(?:flu\s+)?season|this\s+(?:past\s+)?year)')
BARE_TIME = r'(?:(?:last|this|past)\s+(?:flu\s+)?season|(?:last|this)\s+year)'
NUMWORD = r'\d+|one|two|three|four|five|six|seven|eight|nine|ten'

# [(name, pattern, template)] applied in order, each searched in the whole question (case-insensitive); the matched span is replaced.
FRAMES = [
    # politeness and wrappers: "Can you show me ...", "I'd like to know ...", "Please tell me ..."
    ('polite', r'^(?:please\s+)?(?:(?:can|could|would|will)\s+(?:you|i|we)\s+(?:please\s+)?|i(?:\'d| would)\s+like\s+(?:to\s+(?:know|see)\s+)?|i\s+(?:want|need)\s+(?:to\s+know\s+)?|let\'s\s+see\s+|do\s+we\s+know\s+)(?P<rest>.+)$',
     '{rest}'),
    ('tell_me_wh', r'^(?:please\s+)?(?:tell|show|give)\s+me\s+(?=(?:what|which|who|how|whether|if|when|where)\b)(?P<rest>.+)$', '{rest}'),
    ('tell_me_the', r'^(?:please\s+)?(?:tell|show|give)\s+me\s+(?:the\s+|our\s+)?(?P<rest>(?:rate|percent|percentage|share|proportion|fraction|number|count|average|mean|total|top|bottom|medical loss)\b.+)$',
     'What is the {rest}'),
    # numbers: "65+" -> "65 and older" (an age) or "3 or more" (a count); "between 50 and 74" -> "50 to 74"; "last 2 years" -> "last two years"
    ('plus_number', r'(?<![\w.$(,])(?P<n>\d+)\s*\+(?!\s*\))', '@'),
    ('lab_n_or_more', r'\b(?P<lab><LAB>)\s+(?P<n>\d+(?:\.\d+)?)\s+or\s+(?P<d>higher|above|more|greater|lower|below|less)\b', '{lab} of {n} or {d}'),
    ('says_takes', r'\b(?:said|says|stated|states|told\s+us)\s+(?:that\s+)?(?:they|he|she)\s+(?:take|takes|are\s+taking|is\s+taking|use|uses)\b', 'reports taking'),
    ('why_did_stop', r'^why\s+did\s+(?:they|he|she|the\s+\w+)\s+(?:stop|discontinue)\s+(?P<d>\w+)\s+for\s+(?P<who>ZZID\d+ZZ)$', 'Why was {d} stopped for {who}'),
    ('filled_rx', r'\bfill(?:ed|s)?\s+(?:an?\s+)?(?P<d><DRUG>)\s+prescriptions?\b', 'filled a {d}'),
    ('date_of', r'^(?:the\s+)?date\s+of\s+(?P<rest>.+)$', 'When was {rest}'),
    ('change_in', r'\b(?P<lab><LAB>)\s+change\b', 'change in {lab}'),
    ('possessive_claims', r'(?P<who>ZZID\d+ZZ)\'s\s+claims\b', 'claims for {who}'),
    ('why_possessive_stopped', r'^why\s+(?:was|were)\s+(?P<who>ZZID\d+ZZ)\'s\s+(?P<d>\w+)\s+stopped$', 'Why was {d} stopped for {who}'),
    ('reason_given', r'^what\s+reason\s+is\s+given\s+for\s+(?P<d>\w+)\s+being\s+stopped\s+in\s+(?P<who>ZZID\d+ZZ)\'s\s+chart$', 'What was the reason {d} was stopped for {who}'),
    ('reason_for_stopping', r'^what\s+was\s+the\s+reason\s+for\s+stopping\s+(?P<d>\w+)\s+for\s+(?P<who>ZZID\d+ZZ)$', 'What was the reason {d} was stopped for {who}'),
    ('reason_in_chart', r'^reason\s+(?P<d>\w+)\s+was\s+stopped\s+in\s+(?P<who>ZZID\d+ZZ)\'s\s+chart$', 'What was the reason {d} was stopped for {who}'),
    ('between_and', r'\bbetween\s+(?P<a>\d+)\s+and\s+(?P<b>\d+)\b', '{a} to {b}'),
    ('years_in_words', r'\b(?P<w>last|past|previous)\s+(?P<n>[2-9]|10)\s+(?P<u>years?)\b', '@'),
    # a sub-phrase naming the data source moves to the end: "Using the purchased SDOH data, which ...?" / "per the purchased SDOH data"
    ('fronted_source', r'^(?:using|per|based\s+on|according\s+to|from)\s+(?:the\s+)?(?P<src>(?:purchased|vendor|received)\s+[^,]+?),\s*(?P<rest>.+)$', '{rest} according to the {src}'),
    ('per_source', r'\bper\s+(?=(?:the\s+)?(?:purchased|vendor|received)\b)', 'according to '),
    # fronted time phrase moves to the end: "In 2029, how many ...?" / "Over the last two years, what percentage ...?"
    ('fronted_time', r'^(?P<time>' + TIME + r'|' + BARE_TIME + r')\s*[,:\-–—]\s*(?P<rest>.+?)$', '{rest} {time}'),
    # "Number of X", "Count of X", "What number of X", "Count X" -> "How many X"
    ('count_of', r'^(?:what(?:\'s|\s+is|\s+was|\s+are)?\s+)?(?:the\s+)?(?:total\s+)?(?:number|count|tally|quantity)\s+of\s+(?P<rest>.+)$', 'How many {rest}'),
    ('what_number_of', r'^what\s+number\s+of\s+(?P<rest>.+)$', 'How many {rest}'),
    ('count_x', r'^count\s+(?!of\b)(?:the\s+|all\s+|all\s+of\s+the\s+)?(?P<rest>.+)$', 'How many {rest}'),
    # measures of proportion: "Percent of X ...", "Percentage of X ...", "Proportion of X ..." -> "What share of X ..."
    ('share_head', r'^(?:what(?:\'s|\s+is|\s+was|\s+are|\s+were)?\s+)?(?:the\s+|our\s+)?(?:overall\s+)?(?:percent(?:age)?|proportion|fraction|share|%)\s+of\s+(?P<rest>.+)$',
     'What share of {rest}'),
    ('share_who', r'^(?P<head>what share of .+?)\s+(?:who|that|whom)\s+(?P<rest>(?:have|has|had|are|is|were|was|got|received|filled|completed|went|take|took|did|do|does)\b.+)$',
     '{head} {rest}'),
    # "among X, how many Y" / "of X, what share Y" -> "how many of X Y" / "what share of X Y"
    ('among_how_many', r'^(?:among|of|out of|for)\s+(?:the\s+)?(?P<pop>[^,]+?),\s*(?:how many|what number)\s+(?:of them\s+)?(?P<rest>.+)$', '@'),
    ('among_share', r'^(?:among|of|out of|for)\s+(?:the\s+)?(?P<pop>[^,]+?),\s*(?:what\s+)?(?:share|percent(?:age)?|proportion|fraction)\s+(?:of them\s+)?(?P<rest>.+)$',
     'What share of {pop} {rest}'),
    # scope words that name the default population
    ('default_scope', r'\s+(?:in|across|within)\s+(?:our|the)\s+(?:patient\s+)?(?:population|panel|system|health\s+system|network|data|book(?:\s+of\s+business)?)\b', ' '),
    ('adults_with', r'\badults\s+with\b', 'adult patients with'),
    # negations the grammar spells "have no X" / "haven't had X"
    ('never_had', r'(?P<pre>.*?)\b(?:didn\'t|did not|haven\'t|have not|hasn\'t|has not|never)\s+(?:get|got|gotten|receive|received|undergo|underwent|have|had|obtain|obtained)\s+(?P<post>.+)$',
     "{pre} haven't had {post}"),
    ('gone_without', r'(?P<pre>.*?)\b(?:have\s+|has\s+|had\s+)?(?:gone|went|going)\s+without\s+(?:an?\s+|any\s+|the\s+)?(?P<post>.+)$', '{pre} have no {post}'),
    ('lacking', r'(?P<pre>.*?)\b(?:lacking|lacks|lack)\s+(?P<post>.+)$', '{pre} without {post}'),
    ('not_filled', r'(?P<pre>.*?)\b(?:did\s+not|didn\'t|have\s+not|haven\'t|has\s+not|hasn\'t|never)\s+fill(?:ed)?\s+(?:an?\s+|any\s+|a\s+)?(?P<x>.+?)(?P<tail>\s+(?:in|during|over|within)\b.*)?$',
     '{pre} have no {x} fill {tail}'),
    ('not_on', r'(?P<pre>.*?)\b(?:are|is)\s+(?:not|n\'t)\s+(?:currently\s+)?(?:taking|using|receiving)\s+(?P<post>.+)$', '{pre} are not on {post}'),
    ('on_neither', r'(?P<pre>.*?)\b(?:on|taking)\s+neither\s+(?:an?\s+)?(?P<a>.+?)\s+nor\s+(?:an?\s+)?(?P<b>.+?)(?P<tail>[?.\s—–-]*)$', '{pre} not on an {a} or {b} {tail}'),
    ('do_not_take', r'(?P<pre>.*?)\b(?:do\s+not|don\'t|does\s+not|doesn\'t)\s+(?:take|use)\s+(?P<post>.+)$', '{pre} are not on {post}'),
    # drugs: "take / use / receive X" -> "are on X"; "have no X" -> "are on no X"
    ('have_no_drug', r'\b(?:have|has)\s+no\s+(?P<d><DRUG>)\b(?!(?:\s*,?\s*(?:or|and)?\s*<DRUG>)*\s+fills?\b)', 'are on no {d}'),
    ('is_taking_drug', r'\b(?P<aux>is|are|was|were)\s+(?P<subj>[\w-]+)\s+(?:taking|using|receiving)\s+(?P<art>an?\s+|any\s+)?(?P<d><DRUG>)\b', '{aux} {subj} on {art} {d}'),
    ('does_take_drug', r'\b(?:does|do)\s+(?P<subj>[\w-]+)\s+(?:take|use|receive)\s+(?P<art>an?\s+|any\s+)?(?P<d><DRUG>)\b', 'is {subj} on {art} {d}'),
    ('take_drug', r'(?<!report )(?<!reports )(?<!reported )(?<!say )\b(?:(?:are|is|were|was)\s+)?(?:take|takes|taking|use|uses|using|receive|receives|receiving)\s+(?P<art>an?\s+|any\s+)?(?P<d><DRUG>)\b', 'are on {art} {d}'),
    # going to the ED is having an ED visit
    ('went_to_ed', r'\b(?:went|go|goes|gone|going|presented|came|come|visited|visit|visits)\s+(?:to\s+|at\s+)?(?:the\s+)?ED\b(?!\s+visits?)', 'had an ED visit'),
    # screening results
    ('positive_screen_with', r'\bwith\s+an?\s+positive\s+(?P<need>[a-z][a-z ]*?)\s+screen(?:ing)?\b', 'who screened positive for {need}'),
    ('positive_screen', r'\b(?:had|have|has)\s+an?\s+positive\s+(?P<need>[a-z][a-z ]*?)\s+screen(?:ing)?\b', 'screened positive for {need}'),
    ('noshow_times', r'\b(?P<lead>also\s+|and\s+|with\s+|who\s+)?(?:had\s+|have\s+|has\s+|missed\s+)?(?:at\s+least\s+(?P<n1>' + NUMWORD + r')(?:\s+or\s+more)?|(?P<n2>' + NUMWORD + r')\s+or\s+more)\s+(?:no-shows|missed\s+appointments|appointments\s+\(no-shows\))', '@'),
    ('who_among', r'^who\s+among\s+(?P<pop>.+?)\s+(?P<v>lives|has|have|had|is|are|was|were|screened|filled|got|received)\s+(?P<rest>.+)$', '@'),
    ('lab_threshold_on', r'\b(?P<op>above|over|below|under|at\s+least|greater\s+than|less\s+than)\s+(?P<n>\d+(?:\.\d+)?)\s+on\s+(?P<lab><LAB>)\b', 'with {lab} {op} {n}'),
    ('flu_rate', r'^(?:what(?:\'s|\s+is|\s+was)\s+)?(?:the\s+|our\s+)?(?:flu|influenza)\s+(?:vaccination|vaccine|immunization|shot)\s+rate\s+(?:for|among|in)\s+(?P<pop>.+?)(?P<w>\s+(?:in\s+the\s+)?(?:last|past|this)\s+(?:flu\s+)?season)?$', 'What share of {pop} got a flu shot {w}'),
    # blood pressure control, lab on the latest result
    ('whose_bp', r'\bwhose\s+blood\s+pressure\s+(?:is|are)\s+(?P<s>controlled|under\s+control|uncontrolled|not\s+controlled)', 'have blood pressure {s}'),
    ('not_controlled', r'\bblood\s+pressure\s+not\s+controlled\b', 'blood pressure not under control'),
    ('lab_on_latest', r'\b(?:are|is|were|was)\s+(?P<op>above|over|below|under|at\s+least|greater\s+than|less\s+than|higher\s+than|lower\s+than)\s+(?P<n>\d+(?:\.\d+)?)\s+on\s+(?:their\s+)?(?P<w>latest|most\s+recent|last)\s+(?P<lab><LAB>)\b',
     'have a latest {lab} {op} {n}'),
    # "which patients are frequent ED users, meaning 3 or more ED visits" -> "which patients had 3 or more ED visits"
    ('defined_as', r'^(?P<wh>which|what|who)\s+(?:patients\s+)?(?:are|is)\s+[^,]*?,\s*(?:meaning|defined\s+as|i\.e\.|which\s+means|that\s+is)\s+(?:patients\s+)?(?:with\s+|who\s+(?:have|had)\s+)?(?P<rest>(?:at\s+least\s+)?(?:\d[\d,]*|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:or\s+more\s+)?.+)$',
     'Which patients had {rest}'),
    ('have_had_no', r'\bhave\s+had\s+no\b', 'have no'),
    ('share_patients_on', r'^(?P<head>what share of .+?\bpatients)\s+on\s+', '{head} are on '),
    ('with_completed', r'\bwith\s+(?:an?|the)\s+completed\s+(?P<x>[a-z ]+?)\s+(?P<t>(?:in|during)\s+20\d\d)\b', 'who completed an {x} {t}'),
    ('rate_slots', r'^(?P<thing>[^,]*?\b(?:rate|coverage)),\s*(?P<pop>[^,]+),\s*(?:(?:in|over|during|within)\s+)?(?:the\s+)?(?P<win>(?:past|last)\s+.+)$',
     '{thing} for {pop} in the {win}'),
    ('top_n_by', r'^(?:show\s+(?:me\s+)?|list\s+|give\s+me\s+|what\s+(?:are|were)\s+)?(?:the\s+)?(?P<sup>top|bottom|highest|lowest)\s+(?P<n>' + NUMWORD + r')\s+'
     r'(?P<dim>[a-z]+)\s+(?:by|for|in terms of|on)\s+(?:the\s+)?(?:number\s+of\s+)?(?P<m>.+?)(?:\s*,?\s*(?:with|and)\s+(?:the\s+)?(?:counts?|numbers?|totals?))?$',
     'Which {n} {dim} had the {sup} {m}, and how many'),
    ('how_many_last', r'^(?P<a>.+?),?\s+and\s+how\s+many(?P<t>\s+(?:in|during|over|within|for)\s+.+)$', '{a} {t}, and how many'),
    ('bare_time_end', r'^(?P<pre>.+?)(?<!\bin)(?<!\bthe)(?<!\bduring)(?<!\bover)(?<!\bwithin)(?<!\bfor)\s+(?P<t>last\s+(?:flu\s+)?season|this\s+(?:flu\s+)?season|last\s+year|this\s+past\s+year)$', '{pre} in the {t}'),
    # ---- spend, claims and measures asked as a fragment or with other verbs ------------------------------------------------------
    ('fronted_by', r'^by\s+(?P<d>[a-z][a-z -]*?),\s*(?P<rest>.+)$', '{rest} by {d}'),
    ('spend_fragment', r'^(?P<y>20\d\d)\s+(?P<x>medical\s+claims|pharmacy\s+claims|claims)\s+paid,?\s+by\s+(?P<d>.+)$', 'What did we pay out in {x} in {y} by {d}'),
    ('measure_fragment', r'^(?!(?:what|which|who|whom|how|when|where|why|is|are|was|were|do|does|did|has|have|had|can|could|list|show|find|compare|rank|explain|prioriti[sz]e|identify|give|get|top)\b)'
     r'(?P<x>(?:the\s+|our\s+)?(?:total|average|median|overall|rate|top|bottom|out\s+of\s+network|generic|medical\s+loss)\b.+)$', 'What is the {x}'),
    ('oon_share', r'^what\s+is\s+(?:the\s+|our\s+)?out\s+of\s+network\s+(?:share|percent(?:age)?|rate|proportion)\s+of\s+(?P<x>.+)$', 'What share of {x} went out of network'),
    ('how_much_of_pct', r'^how\s+much\s+of\s+(?P<x>.+?),\s*as\s+a\s+(?:percent(?:age)?|share|proportion),\s*(?P<rest>(?:was|were|is|are|went|goes|comes?)\b.+)$', 'What share of {x} {rest}'),
    ('year_rate_fragment', r'^(?P<y>20\d\d)\s+(?P<x>[a-z][a-z -]*?\brate\b.*)$', 'What is the {x} in {y}'),
    ('rate_rank', r'^(?=.*\b(?:payers?|practices?)\b)(?=.*\b(?:highest|lowest|largest|worst|best|most)\b)(?=.*\b(?:denial\s+rate|denies\s+the\s+\w+\s+share\s+of\s+claims)\b)(?=.*\b20\d\d\b)(?=.*\b\d[\d,]*(?:\s+or\s+more|\+)?\s+claims\b).+$', '@'),
    ('high_cost_list', r'^(?=(?:list|show|which|who|identify|what\s+patients|give)\b)(?!.*\b(?:share|percent|how\s+many)\b)(?=.*\bhigh-cost\s+claimants?\b)(?=.*(?:\$\s?[\d,]+))(?=.*\b20\d\d\b).+$', '@'),
    # ---- insight engines: the opening each family reads ---------------------------------------------------------------------------
    ('denial_why', r'^(?:explain|what\'s\s+causing|what\s+is\s+causing|what\'s\s+behind|what\s+is\s+behind)\s+the\s+(?:high\s+)?denial\s+rate\s+(?:for|with|at)\s+(?:our\s+|the\s+)?worst\s+payer$',
     'Why is our denial rate so high with our worst payer'),
    ('denial_why_deny', r'^why\s+(?:does|do)\s+(?:our|the)\s+worst\s+payer\s+deny\s+so\s+many\s+claims$', 'Why is our denial rate so high with our worst payer'),
    ('driving_behind', r'^(?:what\'s|what\s+is)\s+(?:behind|causing|responsible\s+for|the\s+driver\s+of|the\s+cause\s+of)\s+', 'What is driving '),
    ('driving_factors', r'^(?:what|which)\s+(?:factors|things|drivers|reasons|causes)\s+(?:drive|explain|are\s+driving|lie\s+behind|are\s+behind)\s+(?P<x>.+)$', 'What is driving {x}'),
    ('driving_explain', r'^(?:explain|describe|tell\s+me)\s+what(?:\'s|\s+is)\s+driving\s+(?P<x>.+)$', 'What is driving {x}'),
    ('why_change_noun', r'^(?:what\s+(?:caused|drove|explains|explained)\s+the|why\s+(?:was|is)\s+there\s+an?)\s+(?:(?P<y1>20\d\d)\s+)?(?P<n>jump|rise|increase|spike|growth|climb|drop|decline|fall|change|surge)\s+in\s+(?P<x>.+?)(?:\s+(?:in|during)\s+(?P<y2>20\d\d))?$', '@'),
    ('why_change_verb', r'^(?P<x>.+?)\s+(?P<v>went\s+up|jumped|rose|increased|spiked|climbed|grew|dropped|fell|declined|surged)(?:\s+(?:sharply|significantly|a\s+lot|substantially|dramatically))?\s+(?P<t>in\s+20\d\d)\s*[—–-]+\s*why$', '@'),
    ('gap_by_group', r'^(?:where|what)\s+(?:are|is)\s+the\s+(?P<sup>largest|biggest|greatest|worst)\s+gaps?\s+in\s+(?P<m>.+?)\s+(?:by|across|among)\s+(?:our\s+)?(?P<g>practices|practice|clinics|sites|payers|lines?\s+of\s+business)$', 'Which {g} have the {sup} {m} gap'),
    ('furthest_behind', r'^which\s+(?P<g>practices|clinics|sites|payers)\s+(?:are|is)\s+(?:furthest|farthest)\s+behind\s+(?:on|in)\s+(?P<m>.+)$', 'Which {g} have the worst {m}'),
    ('rank_by', r'^rank\s+(?P<g>practices|clinics|sites|payers)\s+by\s+(?:how\s+big\s+)?(?:their\s+)?(?P<m>.+?)(?:\s+is)?$', 'Which {g} have the biggest {m}'),
    ('across_group', r'^across\s+(?:our\s+)?(?P<g>practices|clinics|sites|payers|lines?\s+of\s+business),\s*(?P<rest>.+)$', '{rest} across {g}'),
    ('groups_with', r'^(?:show|list|find|identify|name)\s+(?:me\s+)?(?:the\s+)?(?P<g>practices|payers|clinics|sites)\s+with\s+(?:the\s+)?(?P<rest>.+)$', 'Which {g} have the {rest}'),
    ('prioritize_for', r'^prioriti[sz]e\s+(?P<pop>.+?)\s+for\s+(?:care\s+team\s+)?(?:outreach|calls?|follow-?up)(?:\s+calls?)?$', 'Which {pop} should the care team call first'),
    ('recent_discharge_call', r'^(?:who|which\s+patients)\s+(?:was|were|is|are)\s+recently\s+discharged\s+and\s+(?:needs?|need)\s+a\s+call(?:\s+now|\s+right\s+now)?$', 'Which recently discharged patients need a call right now'),
    ('list_to_call', r'^(?:list|show|find|name|identify)\s+(?:the\s+)?(?P<pop>.+?)\s+to\s+call\s+(?P<t>right\s+now|now|first)$', 'Which {pop} should we call {t}'),
    ('concentration', r'^(?=.*\b(?:concentrat\w+|few\s+percent)\b)(?=.*\b(?:spend|cost|costs)\b)(?=.*\bpatients\b)(?!.*\d).+$',
     'How concentrated is our medical spend — how much comes from the top few percent of patients'),
    ('yoy_how_change', r'^how\s+did\s+(?P<m>.+?)\s+change\s+(?:between|from)\s+(?P<a>20\d\d)\s+(?:and|to)\s+(?P<b>20\d\d)(?P<rest>.*)$', 'Did {m} go up or down from {a} to {b}{rest}'),
    ('yoy_versus', r'^(?P<m>.+?)\s+(?P<b>20\d\d)\s+(?:versus|vs\.?|compared\s+(?:to|with))\s+(?P<a>20\d\d)\s*[:,—–-]*\s*(?:(?:go\s+)?up\s+or\s+down\s*,?\s*)?(?P<rest>.*)$', 'Did {m} go up or down from {a} to {b} {rest}'),
    ('need_tied', r'^(?:which|what)\s+social\s+needs?\s+(?:is|are)\s+(?:the\s+)?(?:most\s+)?(?:strongly\s+|closely\s+)?(?P<v>tied|linked|related|associated)\s+(?:to|with)\s+(?P<o>.+)$', 'Which social need is most {v} to {o}'),
    ('need_tied_rev', r'^(?P<o>no-shows|missed\s+appointments)\s+(?:are|is)\s+(?P<v>linked|tied|related|associated)\s+(?:most\s+)?(?:strongly\s+)?(?:to|with)\s+which\s+social\s+needs?$', 'Which social need is most {v} to {o}'),
    ('need_link', r'^which\s+social\s+needs?\s+has\s+the\s+(?:strongest|closest|biggest|greatest)\s+(?:link|association|relationship|connection)\s+(?:to|with)\s+(?P<o>.+)$', 'Which social need is most linked to {o}'),
    ('lead_time_noshow', r'^(?=.*\b(?:book\w*|schedul\w*|lead\s+times?)\b)(?=.*\b(?:further|farther|longer|ahead|advance|out)\b)(?=.*\b(?:no-?shows?|miss\w*)\b)(?!.*\d).+$',
     'Does booking further out make people more likely to no-show'),
    ('compare_for', r'^compare\s+(?P<a>[\w-]+)\s+(?:and|with|to|versus|vs\.?)\s+(?P<b>[\w-]+)\s+(?:for|on)\s+(?P<m>.+)$', 'How does {a} compare with {b} on {m}'),
    ('versus_how', r'^(?P<a>[\w-]+)\s+(?:versus|vs\.?)\s+(?P<b>[\w-]+)\s*[:,—–-]\s*how\s+do(?:es)?\s+(?P<m>.+?)\s+(?:differ|compare|look)$', 'How does {a} compare with {b} on {m}'),
    ('look_for', r'^how\s+do\s+(?P<m>.+?)\s+look\s+for\s+(?P<a>[\w-]+)\s+(?:relative\s+to|compared\s+(?:to|with)|versus|vs\.?)\s+(?P<b>[\w-]+)$', 'How does {a} compare with {b} on {m}'),
    ('svi_compare', r'^(?P<m>.+?)\s*[:,]\s*(?P<a>high[- ]svi[^:]*?)\s+(?:versus|vs\.?|compared\s+(?:to|with)|and)\s+(?P<b>low[- ]svi.*)$', 'Compare {m} between {a} and {b} patients'),
    # list / who wrappers keep the noun phrase: the grammar reads "List X", "Which X"
    ('who_has_but_not', r'^who\s+(?:has|have)\s+(?:both\s+)?(?P<c>.+?)\s+(?:but|and)\s+(?:isn\'t|is\s+not|aren\'t|are\s+not)\s+(?P<neg>.+)$', 'Which patients with {c} are not {neg}'),
    ('which_pop_dash', r'^(?P<pop>.+?)\s*[—–-]+\s*which\s+(?:patients|members|ones)$', 'Which {pop}'),
    ('which_pop_who', r'^(?P<head>which\s+\S+(?:\s+(?:with|and)\s+\S+){0,3})\s+who\s+(?P<rest>(?:are|have|had|were)\b.+)$', '{head} {rest}'),
    ('show_me', r'^(?:please\s+)?(?:show|give|get|pull\s+up|display|find|identify|provide|return|bring\s+up)\s+(?:me\s+)?(?:the\s+|a\s+)?(?:list\s+of\s+|listing\s+of\s+)?(?P<rest>(?:patients|members|diabetics|practices|payers|claims|prescriptions|the\s+patients|high-cost).+)$',
     'List {rest}'),
    # "X is attributed to which practice" -> "Which practice is X attributed to"
    ('attributed_to', r'^(?P<subj>.+?)\s+is\s+attributed\s+to\s+which\s+(?P<what>practice|clinician|pcp|provider)$', 'Which {what} is {subj} attributed to'),
]

# words the grammar's quotes cannot reach but that carry no content (politeness, filler): dropped only at the very start or end
FILLER_LEAD = r'^(?:so|ok|okay|hey|hi|hello|well|also|now|then|and then)\s*[,:]?\s+'
FILLER_TAIL = r'\s*(?:please|thanks|thank you|for me)\s*$'

SUPERLATIVES = {'top': 'most', 'highest': 'most', 'bottom': 'fewest', 'lowest': 'fewest'}  # "top 3 practices by X" -> "the most X"
