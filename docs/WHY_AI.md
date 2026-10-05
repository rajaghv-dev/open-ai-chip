# Why these designs are AI, and not just a program or a logic circuit

This repository has three tiny chips: `vision_all_lit`, `vision_block` and `text_sentiment`. Each one does a job
you could also do with one line of code or a few logic gates. So what makes them "AI"?

The short answer: **nobody wrote the rule. The rule was learned from labelled examples, and it is stored as numbers
(weights) inside a fixed, general-purpose structure (a neuron).** Change the examples and the numbers change. The
hardware stays the same.

This document explains that idea with the three designs, then extends it with computed examples that are not built
as chips yet: audio (section 6), a tiny transformer (section 7) and number precision from fp32 down to 1 bit
(section 8). Every number here comes from the repository: the
fitter `model/tiny_ai/train.py`, the result `model/tiny_ai/weights.json`, and the RTL in `designs/<name>/rtl/`.

---

## 1. Three ways to build the same thing

Take the first task: *a 2 x 2 black-and-white image; answer 1 if all four pixels are lit.*

**Traditional programming.** A person writes the rule:

```python
def all_lit(p):            # p = [p0, p1, p2, p3], each 0 or 1
    return p[0] and p[1] and p[2] and p[3]
```

**A traditional digital circuit.** A person draws the rule as gates: one 4-input AND gate.

```
p0 ─┐
p1 ─┤
p2 ─┤ AND ── answer
p3 ─┘
```

**AI (what this repository does).** Nobody writes the rule. Instead:

1. Pick a general structure that can express many rules: a **neuron**.
2. Give it **labelled examples**: all 16 possible images, each tagged with the right answer.
3. Let a **training** procedure find the neuron's numbers so that it gives the right answer on the examples.
4. Build the neuron in hardware, with the learned numbers stored in a small table (a ROM).

In the first two approaches, the *knowledge* (the rule) is in the code or the wiring. In AI, it is in the **data**
(the examples) and ends up in the **weights**. That is the whole difference.

---

## 2. What a neuron is

A neuron is a very simple calculation:

1. Look at each input.
2. Multiply each input by its **weight** (how much it matters, and in which direction).
3. Add everything up. That total is the **score**.
4. If the score passes a **threshold**, answer 1. Otherwise answer 0.

```
   input 0 ──(× weight 0)──┐
   input 1 ──(× weight 1)──┤
   input 2 ──(× weight 2)──┼──► add ──► score ──► score ≥ threshold ? ──► 1 or 0
   input 3 ──(× weight 3)──┘
```

The structure never changes. Only the weights and the threshold do. Different weights make the same neuron do a
different job. This is the building block of every neural network, from these toy chips up to the MNIST digit
classifiers in `../open-ai-silicon` and, at a far larger scale, image and language models.

In the vision designs the inputs and weights are single bits, so "multiply and add" becomes "**count how many
pixels match their weight**". That is a binary neuron, the same kind used in `bnn_mnist`.

---

## 3. The three designs

### 3.1 `vision_all_lit`: a dense neuron (every input has its own weight)

**Task.** A 2 x 2 image arrives one pixel at a time. Answer 1 if all four are lit.

**The model.** One binary neuron: score = number of pixels equal to their weight; answer = score ≥ threshold.

**What training found** (`weights.json`): weights `1, 1, 1, 1`, threshold `4`.

**Worked example.** Image `1 1 0 1` (one pixel dark):

| pixel | value | weight | match? |
|---|---|---|---|
| 0 | 1 | 1 | yes |
| 1 | 1 | 1 | yes |
| 2 | 0 | 1 | no |
| 3 | 1 | 1 | yes |

Score = 3. Is 3 ≥ 4? No, so the answer is **0**. The chip also outputs the score (3), so you can see *how close* it
came. A plain AND gate can't tell you that.

**In hardware** (`designs/vision_all_lit/rtl/`): a weight ROM (`vision_all_lit_rom.v`, generated, 4 bits plus the
threshold), a 1-bit "match" gate, a 3-bit counter and a comparator. Pixels are used as they arrive and never stored.
169 standard cells, 10 flip-flops.

### 3.2 `vision_block`: convolution (one small kernel reused everywhere)

**Task.** A 3 x 3 image. Answer 1 if it contains a fully lit 2 x 2 block anywhere.

**The model.** One 2 x 2 neuron, called a **kernel**, is slid over the four positions where a 2 x 2 window fits.
The answer is 1 if the neuron fires at *any* position (this is **max-pooling**). The same four weights are reused at
every position. That reuse is what makes it a **convolution**: the idea behind every image-recognition network.

**What training found:** kernel `1, 1, 1, 1`, threshold `4`.

**Worked example.** Image (rows top to bottom):

```
0 1 1
0 1 1      the four 2x2 windows:   top-left  top-right  bottom-left  bottom-right
0 0 0                              0 1        1 1        0 1          1 1
                                   0 1        1 1        0 0          0 0
                         matches:  2          4          1            2
```

The top-right window scores 4, which reaches the threshold of 4, so the answer is **1**. The score output is 4 (the best window).

**In hardware** (`designs/vision_block/rtl/`): a 9-bit frame register holds the image. A selector picks one window
per clock cycle, and **one** neuron evaluates it, so the neuron is reused four times in four cycles. There are not
four copies of the neuron. Kernel ROM 4 bits. 297 cells, 24 flip-flops. The result arrives 5 cycles after the last
pixel. This is the trade every AI chip makes: fewer compute units used more times (less area), or more units used
once (more speed).

### 3.3 `text_sentiment`: embeddings (words become numbers)

**Task.** A four-word "sentence" from a four-word vocabulary: `PAD` (blank), `GOOD`, `FINE`, `BAD`. Answer 1
(positive) if `GOOD` appears more often than `BAD`.

**The model.** Each word is looked up in a small table to get a number, its **embedding**. The four numbers are
added up. A total above 0 means positive. This is how language models start: words go in, numbers come out of a
learned table. A real model's table has thousands of numbers per word; this one has one.

**What training found:** `PAD = 0`, `GOOD = +1`, `FINE = 0`, `BAD = −1`, decision "sum > 0".

Nobody told the trainer that `GOOD` is good or that `FINE` is neutral. It was only shown 256 labelled sentences. It
*discovered* that `GOOD` pushes up, `BAD` pushes down, and `FINE` and `PAD` don't matter.

**Worked example.** Sentence `GOOD FINE BAD GOOD`:

| word | embedding |
|---|---|
| GOOD | +1 |
| FINE | 0 |
| BAD | −1 |
| GOOD | +1 |
| **sum** | **+1** |

+1 > 0, so the answer is **positive (1)**. The score output is +1.

**In hardware** (`designs/text_sentiment/rtl/`): the embedding ROM (generated, four 3-bit signed numbers), one adder
and a 5-bit accumulator. One word per clock cycle. 200 cells, 12 flip-flops.

---

## 4. The proof that the knowledge is in the data: same hardware, different examples

This is the clearest way to see the difference from traditional design. Keep the neuron exactly as it is, change
**only the labelled examples**, and run the same trainer (`model/tiny_ai/train.py`). These results were computed
with that fitter:

| Labels given to the trainer | What it learned | Hardware change |
|---|---|---|
| all four pixels lit (the real design) | weights 1,1,1,1, threshold 4 | none |
| at least three lit | weights 1,1,1,1, threshold **3** | none, only the ROM value |
| any pixel lit | weights 1,1,1,1, threshold **1** | none |
| exactly the top row lit (`1 1 0 0`) | weights **1,1,0,0**, threshold 4 | none |
| all pixels dark | weights **0,0,0,0**, threshold 4 | none |
| text: `FINE` also counts as positive | `FINE` becomes **+1** | none |
| text: one `BAD` outweighs two `GOOD`s | `BAD` becomes **−2** | none |

With traditional programming, each row is a new program. With traditional logic design, each row is a new circuit
(AND, majority, OR, NOR...). Here each row is the **same circuit** with a different table of numbers, and the
numbers came from examples, not from a person.

---

## 5. What a single neuron cannot learn (and why that matters)

Training doesn't always succeed, and when it fails it tells you something about the **architecture**. The same fitter
finds **no solution** for these:

| Labels | Result | Why |
|---|---|---|
| exactly two of the four pixels lit | no weights work | One neuron draws one straight dividing line between "yes" and "no". "Exactly two" needs two lines: more than one and fewer than three. This is the famous XOR problem. The fix is a second layer of neurons. That is what "deep" in deep learning means. |
| text: positive only if `GOOD` comes *right before* `BAD` | no embeddings work | Adding up word scores ignores **word order**: "good bad" and "bad good" give the same sum. Fixing this is why sequence models and **attention** (the core of today's language models) exist. |

A program or a logic circuit can be made to do anything you can write down. A neural network can do only what its
structure is able to represent. So choosing the structure (how many neurons, layers, attention or not) is the
real design decision. That choice is what the architecture study in `../open-ai-silicon/docs/ARCH_STUDY_PLAN.md`
is about.

---

## 6. Audio: AI on a stream of sound

The vision and text examples get a whole input at once: a 2 x 2 image, a four-word sentence. Sound is different.
It never stops arriving, one sample after another, and the answer depends on **recent history**. This section adds
that idea with two small examples from the architecture plan (`../open-ai-silicon/docs/ARCH_STUDY_PLAN.md`, items
4.3 `audio_pitch` and 4.4 `audio_onset`).

**Important:** these two are **computed by a script, not built as chips yet.** They are the next examples of the
plan. Every number below is printed by `model/examples/audio.py` (Python standard library only, fixed seed, no
downloads, about 1 second). Nothing here was measured on hardware.

Run it: `python3 model/examples/audio.py`

### Example 1: is this a high tone or a low tone? (`audio_pitch`)

**Task.** Samples arrive one bit at a time (just the sign of the waveform: above zero = 1, below = 0). After each new
sample, answer 1 if the last W samples look like a **high tone**.

**The model.** A tiny kernel `[+1, -1]` slid along time marks every **sign change** (the signal flipped). Then count
the changes in the last W samples. A high tone flips more often than a low one, so: count >= threshold means high.

**The data is generated, not downloaded.** Square waves at every phase, two high tones (flip every 2 or 3 samples)
and two low tones (flip every 8 or 12 samples). Each window also appears with one sample flipped, as noise. Label
1 = high tone.

**What training found.** The only learned number is the threshold. The trainer tries every one and keeps the best.

| Window W | Examples | Trained threshold | Accuracy | Delay line | Count register | State bits in all |
|---|---|---|---|---|---|---|
| 8 | 200 | 4 | 85.50% | 7 bit | 3 bit | 11 bit |
| 16 | 200 | 5 | 100.00% | 15 bit | 4 bit | 20 bit |
| 32 | 200 | 7 | 100.00% | 31 bit | 5 bit | 37 bit |

State bits = W-1 change bits in the delay line + 1 bit for the last sample + the count register.

Why is W = 8 worse? Look at the counts the script saw. At W = 8 the high tones give counts 2, 3, 4, 5 and the low
tones give 0, 1, 2, 3. The two ranges overlap, so no single threshold can be perfect: 29 high-tone windows fall below
it. At W = 16 they are apart (high 5, 7, 8; low 1, 2, 3, 4) and at W = 32 even further apart (high 10, 11, 12, 15, 16; low 2 to 6). A
longer window gives a clearer answer and costs more memory. That is the trade this example is for.

**Worked example (W = 8, threshold 4).** A low tone for 10 samples, then a high tone. The model keeps one change
bit per step, and a running count that adds the change entering the window and subtracts the change leaving it.

| t | sample | change | count | decision |
|---|---|---|---|---|
| 0 to 6 | 1 | 0 | 0 | warm-up |
| 7 | 1 | 0 | 0 | low |
| 8 | 0 | 1 | 1 | low |
| 9 | 0 | 0 | 1 | low |
| 10 | 1 | 1 | 2 | low |
| 11 | 1 | 0 | 2 | low |
| 12 | 0 | 1 | 3 | low |
| 13 | 0 | 0 | 3 | low |
| 14 | 1 | 1 | 4 | HIGH |
| 15 | 1 | 0 | 3 | low |
| 16 | 0 | 1 | 4 | HIGH |

(The script prints all 24 steps. It continues to alternate between 4 and 3 because the window is too short, which is
the W = 8 weakness above, seen live.)

**It is a stream.** There is one decision per new sample after the 8-sample warm-up: 17 decisions for 24 samples. The
memory is the window and nothing more: 11 bits, whether the stream lasts 24 samples or a year. The script also checks
that the cheap running count equals a full recount of the last 8 samples at every step (True).

### Example 2: did it just get louder? (`audio_onset`)

**Task.** The input is now a **number** each step, a 4-bit loudness (0 to 15). After each one, answer 1 if the sound
just got louder.

**The model.** One neuron over the last four loudness values. By the plan the weights are `[-1, -1, +1, +1]`: new
loudness minus old loudness.

**Where the knowledge comes from.** We did not tell the trainer those weights. We generated 3,000 windows of random
loudness and labelled a window "louder" when the mean of the newest two exceeded the mean of the older two by 2 or
more (1,119 came out labelled 1). 5% of the training labels were flipped to act as noise. The trainer then searched
**every** weight vector with each weight in -2..2 (625 of them) and every threshold.

| | Result |
|---|---|
| trained weights | `[-1, -1, 1, 1]` |
| trained threshold | score >= 4 |
| rediscovered the plan's weights? | True |
| accuracy on the noisy training labels | 95.73% |
| accuracy on 3,000 fresh clean windows | 100.00% |
| accuracy over all 65536 possible windows | 100.00% |

The trainer found "newest minus oldest" from examples alone, as `text_sentiment` found that `GOOD` pushes up and
`BAD` pushes down. (The label rule I wrote and the shape the trainer found agree because the rule is a weighted sum
of exactly this kind. Of course: that is what we generated.)

**Worked example.** Window of loudness, oldest to newest: `3 4 9 12`.

| energy | weight | product |
|---|---|---|
| 3 | -1 | -3 |
| 4 | -1 | -4 |
| 9 | +1 | +9 |
| 12 | +1 | +12 |
| **sum** | | **+14** |

+14 >= 4, so the answer is **louder**. A steady window `9 10 8 9` gives sum -2, and -2 >= 4 is false: not louder.

**The cost of going from bits to numbers.**

| | audio_pitch (1-bit samples) | audio_onset (4-bit energies) |
|---|---|---|
| input per step | 1 bit | 4 bit |
| history kept for a 4-sample window | 4 bits | 12 bits (3x) |
| the sum | count of 0..3, 2 bits | -30..30, 6 bits, signed |

Numbers instead of bits make the memory wider and the adder larger and signed. The idea is the same.

### How this connects to the other designs and to big systems

- **Delay line vs. frame buffer.** `vision_block` holds a 9-bit frame register because a picture exists all at once.
  Audio has no frame. It keeps a **delay line**: the last W-1 results, with the oldest dropping out as a new one comes
  in. In both cases the state is the thing that grows with how much context the model looks at.
- **Same neuron, new place.** The change detector `[+1, -1]` is a 2-tap kernel like the `vision_block` kernel, but
  slid along **time** instead of across an image. The onset neuron is the `vision_all_lit` neuron with signed
  weights. The threshold is trained, like all the others.
- **A wake-word or keyword spotter is these same pieces, larger.** It computes features over a sliding window of
  sound, applies learned weighted sums, and compares the result with a threshold, with many more features, many
  more neurons and several layers. Real systems usually feed in **spectrogram**-style features (how much energy
  sits at each frequency in each short slice of time) rather than the raw sign bit used here. I have not computed
  any such system here, so I give no accuracy or size for one.
- **Time sharing.** One streaming unit can serve many audio channels if it is fast enough. The plan states that as
  a point about time-multiplexing, not a measurement.

### Honest limits

- **Computed, not built.** There is no RTL, no synthesis and no chip for either example. The state-bit counts are
  counted from the model's description, not from a netlist.
- **Synthetic data.** The "audio" is square waves and random numbers made by the script, not recorded sound. High and
  low tones were chosen to be clearly different.
- **The onset result is partly built in.** The labels came from a rule of the same shape as the neuron, so
  rediscovering `[-1, -1, +1, +1]` shows the search works, not that the shape suits real sound.
- **Noise is mild.** Pitch noise is one flipped sample per window. Real noise is far messier.
- **W = 8 is too short** for this data (85.50%, with the overlapping counts shown above). That is a result, not a bug.
- **Training is exhaustive search** over thresholds and small integer weights. Real systems use gradient descent
  with millions of weights.

---

## 7. Text: a tiny transformer

`text_sentiment` (section 3.3) adds up word numbers and ignores word order. Section 5 showed what that costs: it
cannot learn "positive only if `GOOD` comes right before `BAD`". This section builds, step by step, the pieces that
fix that, and ends with the core of a GPT-style language model.

**Honest status.** None of this is built as a chip yet. Every number below is computed by one script, with no
libraries and no downloads (it runs in under a second):

```bash
python3 model/examples/transformer.py
```

### Step 1: generating text is a loop (`text_bigram`)

A language model's one job is "given what came before, guess the next token". The simplest version looks only at
the last token. Training is counting. The committed training string uses four symbols, `A B C D`:

```
ABCABCABDABCABCD      (16 symbols, so 15 pairs)
```

Count how often each symbol follows each other symbol:

| current \ next | A | B | C | D |
|---|---|---|---|---|
| A | 0 | 5 | 0 | 0 |
| B | 0 | 0 | 4 | 1 |
| C | 3 | 0 | 0 | 1 |
| D | 1 | 0 | 0 | 0 |

The counts add up to 15, one per pair. To predict, take the biggest count in the row (lowest column wins a tie):
after A comes B (score 5), after B comes C (4), after C comes A (3), after D comes A (1).

To **generate**, start at `A` and feed each answer back in as the next input:

```
ABCABCABCABC       (12 tokens: 11 calls to the predictor, one after another)
```

You cannot compute token 5 before token 4 exists. That is why text comes out one token at a time, and why the
time for one token sets the speed. The output repeats after 3 tokens because "biggest count" is deterministic. The
script notes that with argmax the whole model collapses to a 4-entry answer list. The scores are kept because a
real model samples from them.

### Step 2: hard attention (`text_attention`)

Attention means: compare a **query** with stored **keys**, and return the **value** of the best match. Here there
are N = 4 stored pairs with 4-bit keys:

| slot | key | value |
|---|---|---|
| 0 | 1010 | 3 |
| 1 | 0110 | 7 |
| 2 | 1111 | 12 |
| 3 | 0001 | 5 |

Score = number of bits that match. Highest score wins; the lowest slot wins a tie. **Worked example**, query `1011`:

| key | matching bits |
|---|---|
| 1010 | 3 |
| 0110 | 1 |
| 1111 | 3 |
| 0001 | 2 |

Scores are `[3, 1, 3, 2]`. Slots 0 and 2 tie, so slot 0 wins and the output is **3**. The script checks all 16
queries against an independent "smallest Hamming distance" reference: 0 disagreements. Ties happen for 5 of the 16
queries. This is "hard" attention: one winner takes everything.

### Step 3: soft attention, with position

Real transformers use a smooth version. Each token becomes three small vectors: a **Q**uery ("what am I looking
for?"), a **K**ey ("what do I offer?") and a **V**alue ("what do I hand over?"). The recipe:

1. score every pair: `scores[i][j] = Q_i . K_j` (a 4 x 4 grid for four tokens)
2. **softmax** turns each row into weights that are positive and sum to 1 (big score, big weight)
3. output for token i = the weighted sum of the Values

One more ingredient: **position**. Attention on its own only compares contents, so it sees a bag of tokens, the
same flaw as the sum. Each token's embedding therefore also carries a position marker. Here an embedding is 8
numbers: a token one-hot (`PAD GOOD FINE BAD`) followed by a position one-hot (0 to 3).

**Weights are built by hand here, not learned.** The rule: a token's Key is its own position; its Query is "the
position just before me" (times a sharpness S); its Value is 1 if the token is `GOOD`. So each token asks "was the
one before me a GOOD?".

**Trace for `FINE GOOD BAD FINE`** (label 1), with S = 8:

| pos | token | Q (4 numbers) | K | V |
|---|---|---|---|---|
| 0 | FINE | 8 0 0 0 | 1 0 0 0 | 0 |
| 1 | GOOD | 8 0 0 0 | 0 1 0 0 | 1 |
| 2 | BAD | 0 8 0 0 | 0 0 1 0 | 0 |
| 3 | FINE | 0 0 8 0 | 0 0 0 1 | 0 |

The 4 x 4 score grid (row = asking token, column = token looked at) has one 8 per row and zeros elsewhere:

```
pos 0:  8 0 0 0      pos 2:  0 8 0 0
pos 1:  8 0 0 0      pos 3:  0 0 8 0
```

After softmax the 8 becomes weight 0.999 and the zeros become about 0.000. The output per position is
`0.000  0.000  0.999  0.000`: only position 2 (`BAD`) sees that its predecessor is `GOOD`. Readout: for each
position, add "is this token BAD" (0 or 1) to that output. The features are `0.000 0.000 1.999 0.000`; the maximum,
1.999, reaches the threshold 1.5, so the answer is **positive**.

### Step 4: the order test, attention against a bag of words

The label: **positive if a `GOOD` is immediately followed by `BAD`**. Of the 256 four-token sentences, 47 are
positive and 209 are negative. Both models are scored on all 256.

**Attention model.** The Q/K/V weights are the hand-built ones above. Only two numbers were searched, the sharpness
S and the readout threshold, over a small grid (a table of all 20 pairs is printed by the script):

| S | threshold | mismatches / 256 |
|---|---|---|
| 0 (no position) | 1.5 | 41 |
| 1 | 1.5 | 28 |
| 2 | 1.5 | **0** |
| 4 | 1.5 | **0** |
| 8 | 1.5 | **0** |

So with position information the model gets all 256 right (0 mismatches). With position switched off (S = 0), the best
grid result is 41 mismatches. Position is what carries the information about order.

**Bag of words.** Weights for `PAD GOOD FINE BAD`, each from -3 to 3, a threshold from -6 to 6, positive if
sum >= threshold: 31213 settings, all tried. The best gets **216 / 256 correct (84.38%, 40 wrong)**. For scale,
always answering 0 gets 209 / 256. Ten settings tie for best. The reason it cannot reach 100% is plain: `GOOD BAD
FINE FINE` (label 1) and `BAD GOOD FINE FINE` (label 0) contain the same words, so any sum of per-token weights
gives them the same total, and one of the two is always wrong.

| model | mismatches on 256 sentences |
|---|---|
| sum of word weights (best of 31213) | 40 |
| attention, no position (best grid) | 41 |
| attention with position (S = 2, 4 or 8) | **0** |

### Step 5: putting it together

A GPT-style language model is these pieces, repeated and made larger:

- **Embedding lookup** (`text_sentiment`): token to numbers.
- **Position information**: so order matters.
- **Q / K / V and softmax attention**: each token gathers information from the others.
- **The autoregressive loop** (`text_bigram`): predict one token, feed it back, repeat.

What this costs on a chip:

| N (sequence length) | score-grid entries (N x N) | key+value pairs held |
|---|---|---|
| 4 | 16 | 4 |
| 8 | 64 | 8 |
| 16 | 256 | 16 |
| 32 | 1024 | 32 |

- **Compute.** The score grid grows with N squared. Doubling the text length quadruples the work.
- **Memory.** While generating, the keys and values of every earlier token are kept so they need not be recomputed.
  This **KV cache** grows with every token. It is memory, not arithmetic.
- **Parallel or serial** (the trade in the `text_attention` plan): N comparators answer in about one or two cycles
  but cost area; one comparator reused costs little area and takes N cycles.

### Honest limits

- **Computed in Python, not built as chips.** `text_bigram` and `text_attention` are planned designs; nothing here
  is synthesised and no area or timing is measured.
- **The attention weights were constructed by hand.** Only S and the threshold were searched. This shows the
  structure can express the rule; it does not show that training would find it.
- **The order label is one tiny rule** on four tokens with a four-word vocabulary. Passing all 256 sentences is not
  evidence about language.
- **The hard-attention keys and values are an arbitrary committed example.** The 5 ties are decided by the
  lowest-index rule, which is a choice.
- **The N x N and KV-cache numbers are counts of entries in this toy.** They say nothing about the size or speed of
  any real model.
- **Missing from a real transformer:** several heads, many layers, learned weights, multi-number values, scaling
  of scores, and feed-forward blocks.

---

## 8. Numbers: how many bits does a neuron need?

A weight is a number, and a number has to be stored in some **format**: a fixed count of bits. Fewer bits means
less memory and a smaller multiplier, but the stored weight is only an approximation of the one training found.
This section measures that trade on a real (tiny) model. Every number below is printed by
`model/examples/precision.py`, which uses only the Python standard library.

**Run it:** `python3 model/examples/precision.py` (about half a second, always the same output).

### The formats in one minute

A floating-point number is `sign | exponent | mantissa`. The **exponent** sets the size (the range), the
**mantissa** sets the detail (the precision). An integer format has no exponent: it stores a whole number and one
shared **scale** for the whole set of weights (`scale = max|w| / 127` for int8, `/ 7` for int4).

| Format | Sign | Exponent | Mantissa | Largest finite | Smallest subnormal |
|---|---|---|---|---|---|
| fp32 (reference) | 1 | 8 | 23 | - | - |
| bf16 | 1 | 8 | 7 | 3.38953e+38 | 9.18e-41 |
| fp16 | 1 | 5 | 10 | 65504 | 5.96e-08 |
| fp8 E4M3 | 1 | 4 | 3 | 448 | 0.00195 |
| fp8 E5M2 | 1 | 5 | 2 | 57344 | 1.53e-05 |
| int8 / int4 | 1 | none | 7 / 3 | 127 / 7 times the scale | one step of the scale |
| 1-bit | 1 | none | none | one magnitude (mean of all &#124;w&#124;), two signs | - |

Detail: E4M3 has no infinity (only the pattern `S.1111.111` is NaN) and our encoder saturates overflow to 448.
E5M2 is IEEE-like and does have infinity. All rounding is round-to-nearest, ties to even; the script checks the
encoders against Python's own `struct` fp16 and a bit-trick for bf16 (0 mismatches in 2000 values each).

### Worked example: the weight 0.1

`0.1` cannot be stored exactly in binary. Each format keeps the closest value it can:

| Format | Bits (sign / exponent / mantissa) | Stored value |
|---|---|---|
| fp32 | `0 / 01111011 / 10011001100110011001101` | 0.1000000015 |
| bf16 | `0 / 01111011 / 1001101` | 0.1000976562 |
| fp16 | `0 / 01011 / 1001100110` | 0.0999755859 |
| fp8 E4M3 | `0 / 0011 / 101` | 0.1015625000 |
| fp8 E5M2 | `0 / 01011 / 10` | 0.0937500000 |
| int8 (tensor `[0.1, -1.0]`) | `00001101` (integer 13, scale 0.007874) | 0.1023622047 |
| int4 (same tensor) | `0001` (integer 1, scale 0.142857) | 0.1428571429 |
| 1-bit (same tensor) | `0` (positive), scale 0.550 | 0.5500000000 |

bf16 keeps fp32's exponent and cuts the mantissa from 23 bits to 7. E4M3 and E5M2 differ only in where the 7
non-sign bits go: E4M3 spends one on detail, E5M2 spends one on range. The 1-bit weight keeps only the sign,
so `0.1` becomes `0.55`. That is a large change, and the next part shows what it costs.

### Range: why bf16 and E5M2 exist

Precision is detail; range is how big and small a number can get. The same three values in each format:

| Value | bf16 | fp16 | fp8 E4M3 | fp8 E5M2 |
|---|---|---|---|---|
| 70000 | 70144 | inf (overflow) | 448 (saturated) | inf (overflow) |
| 1e-06 | 9.98378e-07 | 1.01328e-06 | 0 (underflow) | 0 (underflow) |
| 1e-09 | 9.96806e-10 | 0 (underflow) | 0 | 0 |

fp16 has good detail but a narrow range: 70000 breaks it, and 1e-09 vanishes. During training, values such as
gradients swing over a huge range, so **bf16** (fp32's range, less detail) is the usual training format. fp8 has
so few bits that you must pick range (E5M2) or detail (E4M3); E5M2 is often used for the wide-ranging gradients
and E4M3 for weights and activations. (This is the common recipe; the script does not test training.)

### The tiny model

Task: a 5 x 5 grey image holds a **vertical bar** (centre column, label 1) or a **horizontal bar** (centre row,
label 0). Each pixel is flipped with probability 0.20, then noise with standard deviation 0.35 is added, so the
answer is not obvious. The data is generated by a small seeded random generator (no downloads).

The model is a single neuron (logistic regression): 25 weights and 1 bias, trained in fp32 with plain gradient
descent on 400 images, then tested on 2000 fresh images. fp32 reaches 94.2% train and 93.2% test accuracy.
Training finds big weights (about 1) on the centre column and row, and small ones (under about 0.3) elsewhere:

```
  0.17   0.30   1.13   0.06   0.13
 -0.10   0.06   1.23  -0.14  -0.17
 -1.05  -1.10  -0.15  -1.13  -1.21
 -0.03  -0.07   1.53   0.06  -0.05
  0.02   0.00   0.89  -0.05   0.10
```

Only the 25 weights are re-stored in each format. The bias, the inputs and the running sum stay full precision,
so the table isolates the effect of **weight** precision.

### Results

| Format | Bits per weight | Weight memory (bits) | Max weight error | Mean weight error | Test accuracy | Same decision as fp32 |
|---|---|---|---|---|---|---|
| fp32 | 32 | 800 | 0 | 0 | 93.25% | 100.00% |
| bf16 | 16 | 400 | 0.003502 | 0.000712 | 93.15% | 99.90% |
| fp16 | 16 | 400 | 0.000404 | 0.000083 | 93.25% | 100.00% |
| fp8 E4M3 | 8 | 200 | 0.050377 | 0.008986 | 93.35% | 99.80% |
| fp8 E5M2 | 8 | 200 | 0.122982 | 0.023126 | 93.45% | 99.40% |
| int8 | 8 | 200 | 0.005985 | 0.002816 | 93.25% | 100.00% |
| int4 | 4 | 100 | 0.102701 | 0.053040 | 93.40% | 98.95% |
| 1-bit | 1 | 25 | 1.092358 | 0.462483 | 82.70% | 85.45% |

How to read it:

- **Down to 8 bits nothing is lost.** Every format from fp32 to int8, E4M3 and E5M2 stays within 0.2 points of
  fp32 accuracy, and at least 99.4% of individual decisions are identical.
- **int4 is still fine here**, with 1.05% of decisions changed. Its weight error is larger (max 0.102701) but
  this neuron has a wide margin, so the errors rarely flip an answer.
- **1 bit costs real accuracy**: 82.70% against 93.25%, and only 85.45% agreement. Binarizing turns the many
  near-zero weights into `+0.436630` or `-0.436630` (the mean of all |w|), which adds noise from all 25 pixels.
  The vision chips here work with 1-bit weights because they were trained for it and their tasks are tiny; a
  float model squeezed to 1 bit afterwards, as done here, is the harder case.
- Accuracy above fp32 (E5M2 93.45%) is luck on 2000 images, not a benefit. Differences of 0.2 points are noise.

### What the bits cost in hardware

| Format | Multiplier partial-product proxy |
|---|---|
| fp32 | 576 (24 x 24, mantissa + implicit 1) |
| fp16 | 121 (11 x 11) |
| bf16 | 64 (8 x 8) |
| int8 | 64 (8 x 8) |
| fp8 E4M3 | 16 (4 x 4) |
| int4 | 16 (4 x 4) |
| fp8 E5M2 | 9 (3 x 3) |
| 1-bit | 1 (one XNOR) |

The proxy is `width x width` for integers and `(mantissa + 1)^2` for floats. It captures the main effect:
a multiplier grows with the **square** of its width. It is **not a synthesized area**: adders, exponent logic,
alignment and rounding are left out. For a measured scale reference, `../open-ai-silicon` reports about 7,466
standard cells for `bnn_mnist` (1-bit) against 531,505 for `cnn_fp16` (float16). Those are different networks,
so the ratio is indicative only.

### How this connects to the chips

- **1-bit** is what `vision_all_lit` and `vision_block` do: "multiply" becomes XNOR, "add" becomes counting the
  matches. It is the smallest and cheapest arithmetic, and needs training that expects 1-bit weights.
- **Small signed integers** are what `text_sentiment` stores: 3-bit signed embedding weights in a small table.
  Integer formats such as int8 and int4 are the common choice for running a trained model, because the table
  above shows little accuracy lost for a quarter or an eighth of fp32's memory.
- **bf16** is the common choice for training, because of its range; **fp8** appears in newer accelerators to
  save more memory and multiplier area.

The trade has three sides: **accuracy** (more bits, closer to fp32), **memory** (bits x weights) and
**multiplier size** (roughly bits squared). The right point depends on the model and the task.

### Honest limits

- One tiny model, one task, one seed. Results on large models, or after training with low precision, differ.
- All formats here are **post-training**: trained in fp32, then rounded. Training in low precision is not tested.
- Only weights are quantized. Real low-precision hardware also quantizes activations and accumulates in wider
  numbers, which adds error not shown here.
- int4, int8 and 1-bit use one scale for the whole tensor. Per-channel or per-group scales usually do better.
- 2000 test images means differences under about 0.5 points are noise.
- Multiplier cost is a counting proxy, not synthesized area; the cell counts come from different networks.
- The bias is kept in fp32 and is not counted in the memory column.

---

## 9. Honest limits of these examples

- **The tasks are deliberately tiny.** For `vision_all_lit`, the learned neuron behaves exactly like an AND gate, and
  a person could have written it by hand. The point is not that AI beats hand design here. The point is that the
  *method* (structure + examples + training + weights in memory) is the same method that scales to tasks no one can
  write by hand. Nobody can write the rule for "is this a picture of a 7?", but the same recipe learns it
  (`bnn_mnist` in `../open-ai-silicon`: 90.10% on 10,000 test digits).
- **Training here is exhaustive search,** because the tasks are so small that every possible weight can be tried.
  Real networks have millions of weights and use gradient descent instead. The idea is the same: adjust the
  numbers until the answers match the examples.
- **The chip is still a digital circuit.** Every AI chip is. What makes it an AI chip is what the circuit is *for*:
  computing weighted sums, reusing them (convolution), looking up embeddings, and holding learned numbers in memory,
  rather than one fixed rule.
- **The chips do inference only.** Training happens in Python on a computer. The chip receives the finished weights
  as a ROM and only runs the model. That is how most AI chips are used: train once, run many times.

---

## 10. How each idea maps to a big AI system

| In these chips | In a large AI model |
|---|---|
| one binary neuron (`vision_all_lit`) | billions of neurons with multi-bit weights |
| one kernel reused at four positions (`vision_block`) | convolution layers in image networks, reused across millions of positions |
| max-pooling (OR of the windows) | pooling layers |
| word → number table (`text_sentiment`) | embedding tables in language models |
| weights in a ROM | weights in on-chip SRAM or off-chip DRAM, the largest cost of real AI chips |
| one neuron used four times in four cycles | the core hardware trade-off: compute units vs. time vs. memory |
| fitted by exhaustive search | trained by gradient descent on large datasets |
| "no solution" for "exactly two lit" or word order | why networks have many layers and attention |

## 11. Try it

```bash
make model-check                        # the learned model matches all 16 + 512 + 256 labelled examples
python3 model/tiny_ai/golden.py text_sentiment 1 2 3 1    # GOOD FINE BAD GOOD -> class 1, score +1
python3 model/tiny_ai/golden.py vision_block 0 1 1 0 1 1 0 0 0   # the 3x3 example above -> class 1, score 4
make simulate DESIGN=vision_block       # the RTL, checked against the model on every possible image
python3 model/examples/audio.py         # section 6: pitch and onset detectors, trained thresholds and weights
python3 model/examples/transformer.py   # section 7: bigram generation, attention, the word-order task
python3 model/examples/precision.py     # section 8: fp32, bf16, fp16, fp8, int8, int4, 1-bit on one model
```

To retrain on different labels, change the label rule in `model/tiny_ai/common.py` (`truth_table`) and run
`make generate`. The ROMs and test vectors are regenerated; the engine RTL is untouched.
