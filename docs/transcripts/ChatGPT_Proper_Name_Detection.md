You: what is the (computationally) cheapest solution to determine whether a string is a proper noun?

ChatGPT:

If you mean **computationally cheapest at runtime**, the cheapest useful heuristic is simply:

```python
def looks_like_proper_noun(s):
    return bool(s) and s[0].isupper()
```

That is effectively **O(1)** if you only inspect the first character. It works surprisingly well in contexts where capitalization is reliable, but it will misclassify sentence-initial words (`"The"`), acronyms (`"NASA"`), and lowercase/stylized names (`"iPhone"`, `"eBay"`).

If you need to actually determine whether something *is* a proper noun rather than merely looks like one, there is no context-free rule that works reliably. For example, `"May"` can be a proper noun or a verb, and `"Apple"` can be a company or a common noun depending on context.

A good cost/accuracy ladder is:

| Method                         |                Runtime cost | Accuracy                         |
| ------------------------------ | --------------------------: | -------------------------------- |
| First character uppercase      |                       ~O(1) | Low                              |
| Capitalization + a few rules   |                        O(n) | Low–moderate                     |
| Hash-set lookup of known names | O(n) hashing / ~O(1) lookup | Moderate, limited vocabulary     |
| POS tagger                     |                 Much higher | Good with sentence context       |
| NER/model/LLM                  |                     Highest | Usually best for ambiguous cases |

For a **single isolated string**, a very cheap practical solution would be something like:

```python
def likely_proper_noun(s):
    s = s.strip()
    return (
        bool(s)
        and s[0].isupper()
        and not s.isupper()       # optionally reject acronyms
    )
```

If this is for processing **millions of strings**, tell me what your input looks like—single words, names such as `"John Smith"`, or words inside sentences—and I can suggest the cheapest approach that meets your required accuracy.


You: by content, not grammatical structure?

ChatGPT:

Yes—if you mean **determine from the string's semantic content itself**, ignoring capitalization, sentence position, etc., then the problem is different.

There is no algorithm that can reliably determine proper-noun status from an arbitrary isolated string without **some external knowledge**. `"apple"`, `"amazon"`, `"jordan"`, `"turkey"`, and `"mercury"` demonstrate why: each can denote either a particular named entity or something else.

The computationally cheapest semantic approach is therefore usually a **dictionary/set lookup**:

```python
proper_nouns = {
    "john", "microsoft", "london", "amazon",
    "jupiter", "shakespeare"
}

def is_known_proper_noun(s):
    return s.casefold() in proper_nouns
```

With a hash set, lookup is **O(1) average** after hashing the string (technically O(n) in string length to compute its hash). Memory is the main cost.

If you need to handle strings you've **never seen before**, however, you need inference rather than lookup. A small classifier/NER model would be the next step, but importantly, an isolated string often doesn't contain enough information to give a logically correct answer. For example:

> `jordan`

By content alone, you can determine that *Jordan is a known proper noun* (person/country/etc.), but you cannot determine that this particular occurrence **is being used as** a proper noun without context.

So if your actual question is **"Does this string correspond to the name of some real/known entity?"**, a hash lookup against an entity/name database is probably the computationally cheapest robust solution.


You: no context - the string will be from a file name. It may or may not have a surname attached. best guess required.

ChatGPT:

Yes. For a **filename-derived string with no context**, where you want a best guess as to whether it represents a **person's name**, possibly `"Firstname"` or `"Firstname Surname"`, I would not use NLP/NER at all.

The cheapest reasonably accurate solution is a **first-name frequency table + optional surname frequency table**. Normalize the filename, split it into tokens, and do hash lookups.

For example, after stripping extension/separators:

`John.jpg` → `john`
`John_Smith.pdf` → `john smith`
`Holiday Photos.zip` → `holiday photos`

Then score approximately:

```text
known first name                         → likely name
known first name + known surname         → very likely name
unknown first name + known surname       → maybe
neither                                  → probably not
```

Even better, store **frequency/probability**, rather than booleans. Then `"James"` scores much higher than an extremely obscure name that happens to occur in your dictionary.

You can make it essentially:

```python
def person_name_score(filename):
    words = normalize(filename).split()

    if not words:
        return 0

    score = first_name_frequency.get(words[0], 0)

    if len(words) >= 2:
        score += surname_frequency.get(words[-1], 0)

    return score
```

Computationally, this is extremely cheap: **O(n)** for filename normalization/hashing and effectively O(1) table lookups. No model loading, tokenization pipeline, embeddings, or inference.

The important distinction is that you're really asking:

**"How name-like is this filename?"**

rather than "Is this grammatically a proper noun?"

For filenames, I'd probably use a tiny weighted heuristic combining **given-name frequency, surname frequency, and a few negative signals** (common filename words such as `invoice`, `report`, `document`, `photo`, `backup`). That will likely give you a substantially better best guess while remaining nearly as cheap as a raw dictionary lookup.

If you tell me the language/country distribution of the filenames, I can suggest a particularly cheap name dataset and scoring formula.


You: multiple languages/countries. every type of name is possible.

ChatGPT:

In that case, I’d change the approach: don’t try to maintain separate “first-name” and “surname” dictionaries. Across all countries and languages, those categories get messy very quickly.

For your use case, the cheapest robust question is:

**“Does this filename look like a human name?”**

Use a single large **multilingual gazetteer of known human-name strings**, then add a tiny amount of fuzzy logic.

A particularly good source is **ParaNames**: it was built from Wikidata and contains about **140 million names across 400+ languages**, with entities classified as `PER`, `LOC`, or `ORG`. ([arXiv][1]) That means you can extract only `PER` names and aliases and build your own compact lookup structure.

At runtime I’d do approximately this:

```python
def human_name_score(filename):
    s = normalize_filename(filename)

    if s in PERSON_NAMES:
        return 1.0

    tokens = split_filename(s)

    if all(t in PERSON_NAME_TOKENS for t in tokens):
        return 0.9

    if any(t in PERSON_NAME_TOKENS for t in tokens):
        return 0.6

    return 0.0
```

`normalize_filename()` should be deliberately conservative: remove the extension, convert `_`/`-` to spaces where appropriate, collapse whitespace, Unicode-normalize, and case-fold **only where case-folding is meaningful**. Do **not** transliterate everything into ASCII; that would destroy distinctions in Arabic, Chinese, Cyrillic, Devanagari, Japanese, Korean, etc.

The interesting optimization is the storage structure. If you have millions of known names, you don't necessarily want a Python `set` containing 140 million Unicode strings. For a binary “probably a name / probably not” decision, a **Bloom filter** is extremely attractive:

```text
filename
   ↓
Unicode normalization
   ↓
exact full-name Bloom filter ── hit → strong YES
   ↓ miss
token Bloom filter ──────────── hits → probable YES
   ↓
NO / uncertain
```

A Bloom filter gives you very cheap membership tests and dramatically lower memory than keeping the strings themselves. False positives are acceptable here because you've explicitly said **best guess**. You can tune the false-positive rate according to your memory budget.

There is also a useful trick that matters for your scenario: build **two filters**.

```text
FULL_PERSON_NAMES
    "محمد علي"
    "李小龙"
    "Jean-Pierre Dupont"
    "Nguyễn Văn An"
    ...

PERSON_NAME_COMPONENTS
    "محمد"
    "علي"
    "李"
    "小龙"
    "Jean-Pierre"
    "Dupont"
    ...
```

Then your score could be:

```python
if full_string in FULL_PERSON_NAMES:
    score = 100
elif len(tokens) >= 2 and all(t in PERSON_NAME_COMPONENTS for t in tokens):
    score = 85
elif len(tokens) == 1 and tokens[0] in PERSON_NAME_COMPONENTS:
    score = 65
else:
    score = 0
```

This handles `"Mohammed"` as well as `"Mohammed Al-Hassan"`, `"王伟"`, `"Олександр Шевченко"`, etc., without needing to know whether each component is technically a given name, patronymic, family name, clan name, or something else.

One important limitation remains unavoidable. Strings such as:

```text
Rose
Jordan
Paris
Victoria
Summer
Hope
Mercedes
Washington
```

can legitimately be people's names **and** non-person strings. With no context, nobody—not even a large language model—can reliably resolve that ambiguity. Your classifier should therefore output a **probability/score**, not pretend the answer is binary.

If CPU is the dominant concern, I'd rank the approaches:

| Approach                    |           Runtime |     Memory |               Multilingual coverage |
| --------------------------- | ----------------: | ---------: | ----------------------------------: |
| Bloom-filter name gazetteer | **Extremely low** |        Low |                           Excellent |
| Hash-set gazetteer          |          Very low |       High |                           Excellent |
| Trie/FST                    |          Very low |     Medium |                           Excellent |
| Small ML classifier         |          Moderate | Low–medium |                            Variable |
| Transformer/NER             |              High |       High | Good, but pointless without context |
| LLM                         |         Very high |          — |                            Overkill |

For **millions of filenames**, my choice would therefore be:

**ParaNames/Wikidata `PER` names → normalize → build a Bloom filter or compact FST → exact full-name lookup + component lookup.**

ParaNames is unusually well matched because it explicitly covers hundreds of languages and labels entities by type rather than assuming Western first-name/surname structure. ([arXiv][1]) Wikidata itself also supports multilingual labels and aliases for people and names. ([Wikidata][2])

And if by “proper noun” you actually mean **any named entity** rather than specifically a person's name, the same idea becomes even better: keep separate filters for `PERSON`, `LOCATION`, and `ORGANIZATION`, and classify the filename with three extremely cheap lookups. ParaNames already provides exactly those three categories. ([arXiv][1])

[1]: https://arxiv.org/abs/2405.09496?utm_source=chatgpt.com "ParaNames 1.0: Creating an Entity Name Corpus for 400+ Languages using Wikidata"
[2]: https://www.wikidata.org/wiki/Help%3ADefault_values_for_labels_and_aliases/en?utm_source=chatgpt.com "Help:Default values for labels and aliases - Wikidata"


You: Surely a classifier is smaller and cheaper than a repository?

ChatGPT: 

Yes—if you care about **memory footprint and deployment size**, a classifier can absolutely be smaller than a large multilingual name repository.

The catch is that with **no context**, a classifier has very little signal to work with. It can only learn patterns in the string itself: character sequences, script, length, affixes, token count, separators, common name morphology, etc. So it becomes a “name-likeness” classifier rather than a true proper-noun detector.

A very cheap option is a **character n-gram linear classifier**. For example, hash character 2–5 grams into a fixed-size feature vector and use logistic regression. That can be tiny—often a few hundred KB to a few MB—and inference is basically just hashing substrings plus a dot product.

Conceptually:

```python
score = sigmoid(
    bias
    + weight["jo"]
    + weight["ohn"]
    + weight["smi"]
    + weight["mith"]
    + ...
)
```

This can learn patterns like `-son`, `-ov`, `-ić`, `bin`, `al-`, common Chinese/Japanese character combinations, etc., without storing every known name.

For your use case, I’d rank the options like this:

| Approach                        | Model/data size |               CPU | Generalizes to unseen names |
| ------------------------------- | --------------: | ----------------: | --------------------------- |
| Huge gazetteer                  |            GBs+ |          Very low | Poor                        |
| Bloom filter                    |      100s MB–GB | **Extremely low** | Poor                        |
| Char n-gram logistic regression |  **~0.5–10 MB** |      **Very low** | **Good**                    |
| Small char CNN                  |        ~1–10 MB |               Low | Better                      |
| Transformer                     |          50 MB+ |              High | Overkill                    |

So yes: **a char n-gram logistic regression is probably the sweet spot** if you want globally multilingual, tiny, fast, and “best guess.”

You'd train it on positives consisting of person names from Wikidata/ParaNames and negatives consisting of ordinary filename-like strings, dictionary words, locations, organizations, document titles, random phrases, etc.

The negative set matters more than the model architecture. If you train only “names vs random garbage,” it will look great in testing and fail badly on real filenames.

One further advantage is that you can use a **feature hashing trick**, so you don't even need to store an n-gram vocabulary. For example, 2^18 hashed features with 8-bit or 16-bit quantized weights could give you a model around **256–512 KB**.

That would likely be my first implementation.


You: As far as context is concerned, why can't I just provide a prompt: is the following string the name of a person?

ChatGPT:

You can. In fact, if you're willing to run a language model, that's probably the simplest solution.

The prompt itself supplies the **task context** that the filename lacks:

> “Is the following string likely to be the name of a person? Answer yes or no: `X`”

The model doesn't need surrounding textual context to make a useful guess. It uses knowledge learned during training plus morphological patterns across languages. So for something like:

* `Aleksandr Petrov` → yes
* `Nguyễn Thị Minh` → yes
* `Kwame` → probably yes
* `Qianyu Zhang` → probably yes
* `Quarterly Financial Report` → no
* `DSC_0042` → no
* `Rose` → uncertain/probably yes

My earlier point about “no context” was about **certainty**, not whether classification is possible. An LLM can absolutely make the best-guess classification you want.

The real question then becomes **cost**. If you're processing, say, 100 filenames, prompting a small language model is perfectly reasonable. If you're processing 100 million filenames, repeatedly invoking an LLM is wasteful compared with a tiny classifier.

There's also a useful middle ground: use a **very small language model** and batch the filenames:

```text
For each string below, estimate whether it is a person's name.
Return only a probability from 0 to 1.

1. Aleksandr Petrov
2. annual report
3. Kwame
4. IMG 2048
5. Nguyễn Thị Minh
...
```

Batching hundreds of filenames per inference makes this substantially cheaper.

And there's an even more interesting possibility given your requirements: **you don't necessarily need a generative LLM at all**. A small pretrained multilingual encoder/classifier can receive essentially the same conceptual task, while being much cheaper locally.

If your objective is specifically **the computationally cheapest model that can make a decent multilingual name/not-name judgment on arbitrary filename strings**, I'd investigate a tiny character-level classifier first, then a small multilingual model if its accuracy isn't sufficient. An LLM would be the easiest baseline against which to measure them.

