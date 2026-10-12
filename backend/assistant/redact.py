"""What a typed question may carry to a model, and into the audit log.

The chatbot sends the question the person typed. On the demo that goes to a
hosted model, and a question routinely names a child ("Tell me about Ana
Reyes"), gives a phone number, or pastes an address. Nothing the chatbot needs
to route the question depends on WHICH child, so the names, numbers and
addresses are taken out before the model sees it and put back, server side,
into the arguments the model returns: the model answers
`get_child_summary(name="CHILD")` and the server looks up the child.

The same masked text is what the audit row keeps. The ISA reads those rows (the
unanswered-questions list), and a question that named a child would otherwise
hand them the child's name from a record that is not theirs.

THE MATCHER KNOWS ONLY THE CHILDREN THE ASKER MAY SEE (`visible_children`,
the one scope rule). Another social worker's child is not in the list, so their
name is not recognised, stays in the question, and the model is never told it
is a child. Using a wider list would turn the masker into a way to ask "is this
name a child in the agency?".

What is masked, and what is deliberately not:

- a child's full name (as recorded, with the middle name, and without it), and
  "Reyes, Ana"; a last name on its own once it is a distinct word of four
  letters or more. NOT a first name on its own: "Maria", "Joy" and "Mark" are
  also words, and the model needs the question to read as a question. A first
  name typed alone therefore still reaches the model; the full name never
  does.
- Philippine mobile numbers in the shapes people type them, and email
  addresses.

Names are compared the way the duplicate check compares them (`_key`: trimmed,
case-folded, NFC), with accents removed on top, so "pena reyes" finds "Peña
Reyes" and "PEÑA" finds "Peña".
"""
import re
import unicodedata

from accounts.scoping import visible_children
from children.duplicates import _key

CHILD, PHONE, EMAIL = "CHILD", "PHONE", "EMAIL"

_WORD = re.compile(r"\w+")

# Mobile numbers as people type them: 09171234567, 0917 123 4567,
# +63 917 123 4567, 639171234567, (0917) 123-4567, 917-123-4567.
_PHONE = re.compile(
    r"(?<!\d)\(?(?:(?:\+?63|0)[\s.\-]?)?9\d{2}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}(?!\d)")
_EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")

# A last name that is one short or ordinary word would take the question's own
# words with it: "Ang" is a surname and also Tagalog for "the", and a masked
# "Sino ang mga bata..." routes to nothing. Under four letters a surname is
# masked only as part of a full name. These are the longer words a question is
# made of.
_MIN_ALONE = 4
_QUESTION_WORDS = frozenset({
    "sino", "bata", "batang", "mga", "ilan", "ilang", "kids", "child", "children",
    "case", "cases", "staff", "user", "users", "today", "tomorrow", "yesterday",
    "week", "month", "year", "this", "that", "with", "what", "which", "when",
    "have", "does", "from", "many", "show", "list", "find", "about", "tell",
    "mayroon", "walang", "kahapon", "bukas", "ngayon", "linggo", "buwan", "taon",
})


def fold(text):
    """`text` as the duplicate check sees it, accents removed as well.

    Returns (folded, origin): for every character of `folded`, the index in
    `text` it came from, so a match can be cut out of the original. Each
    character is folded on its own, which keeps that map exact; whitespace is
    kept as a space, since `_key` would collapse it away and run words
    together.
    """
    out, origin = [], []
    for i, ch in enumerate(text):
        if ch.isspace():
            out.append(" ")
            origin.append(i)
            continue
        for c in unicodedata.normalize("NFKD", _key(ch)):
            if not unicodedata.combining(c):
                out.append(c)
                origin.append(i)
    return "".join(out), origin


def _words(text):
    return tuple(_WORD.findall(fold(str(text or ""))[0]))


def _usable_alone(words):
    """Whether a name of these words may be masked when it stands by itself."""
    if len(words) != 1:
        return bool(words)
    return len(words[0]) >= _MIN_ALONE and words[0] not in _QUESTION_WORDS


class Masker:
    """Masks one request's text, remembers what each token stands for, and
    puts it back into the arguments the model returns.

    One instance per request: it reads the requester's own children once, and
    the tokens it hands out are the ones the model is shown and the audit row
    keeps, so a name means the same token in the question and in the stored
    tool call.
    """

    def __init__(self, request):
        self._phrases = {}           # words -> pks of the children it names
        self._between = {}           # first words -> {last words -> pks}
        self._fullname = {}          # pk -> the recorded full name
        self._longest = 1
        self._load(request)
        self._first_lens = sorted({len(first) for first in self._between})
        self._tokens = {}            # (kind, identity) -> token
        self.mapping = {}            # token -> what goes back in its place

    def _load(self, request):
        rows = visible_children(request).values_list(
            "pk", "fullname", "first_name", "middle_name", "last_name")
        for pk, fullname, first, middle, last in rows:
            self._fullname[pk] = fullname
            full = _words(fullname)
            first, middle, last = _words(first), _words(middle), _words(last)
            if not (first or last) and len(full) >= 2:
                # A record that holds only the composed name (older records,
                # and every test fixture).
                first, last = full[:1], full[-1:]
            # Recorded as written, with and without the middle name, the way
            # a list prints it ("Reyes, Ana"), and the last name alone.
            for phrase in (full, first + middle + last, first + last,
                           last + first, last):
                if _usable_alone(phrase):
                    self._phrases.setdefault(phrase, set()).add(pk)
                    self._longest = max(self._longest, len(phrase))
            if first and last:
                # "Ana M. Reyes" and "Ana Marie Reyes" for a record that holds
                # no middle name, or a different one: one word of any kind
                # between the first name and the last.
                self._between.setdefault(first, {}).setdefault(last, set()).add(pk)
                self._longest = max(self._longest, len(first) + 1 + len(last))

    # --- finding ----------------------------------------------------------

    def _spans(self, text):
        """[(start, end, kind, identity, restore_with)] over `text`, none
        overlapping: mail and numbers first, then names, longest first."""
        found, taken = [], []
        for kind, pattern in ((EMAIL, _EMAIL), (PHONE, _PHONE)):
            for m in pattern.finditer(text):
                if any(m.start() < e and s < m.end() for s, e in taken):
                    continue
                taken.append((m.start(), m.end()))
                typed = m.group()
                identity = typed.lower() if kind == EMAIL else re.sub(r"\D", "", typed)[-10:]
                found.append((m.start(), m.end(), kind, identity, typed))

        if self._phrases:
            folded, origin = fold(text)
            tokens = []
            for m in _WORD.finditer(folded):
                start = origin[m.start()]
                end = origin[m.end() - 1] + 1
                while end < len(text) and unicodedata.combining(text[end]):
                    end += 1       # a decomposed accent belongs to its letter
                tokens.append((m.group(), start, end))
            words = [t[0] for t in tokens]
            i = 0
            while i < len(tokens):
                n, pks = self._longest_at(words, i)
                if n and not any(tokens[i][1] < e and s < tokens[i + n - 1][2]
                                 for s, e in taken):
                    start, end = tokens[i][1], tokens[i + n - 1][2]
                    typed = text[start:end]
                    if len(pks) == 1:
                        # One child: the same child whichever way it was
                        # written, and the recorded name goes back.
                        (pk,) = pks
                        identity, restore = pk, self._fullname[pk] or typed
                    else:
                        identity, restore = tuple(words[i:i + n]), typed
                    found.append((start, end, CHILD, identity, restore))
                    taken.append((start, end))
                    i += n
                else:
                    i += 1

        return sorted(found)

    def _longest_at(self, words, i):
        """(length, pks) of the longest name that starts at words[i], or
        (0, None)."""
        best, pks = 0, None
        for n in range(min(self._longest, len(words) - i), 0, -1):
            found = self._phrases.get(tuple(words[i:i + n]))
            if found:
                best, pks = n, found
                break
        for f in self._first_lens:
            for last, found in self._between.get(tuple(words[i:i + f]), {}).items():
                n = f + 1 + len(last)
                if n > best and tuple(words[i + f + 1:i + n]) == last:
                    best, pks = n, found
        return best, pks

    def _token(self, kind, identity, restore, *, whole):
        """The token for this thing: the same one each time it comes up."""
        token = self._tokens.get((kind, identity))
        if token is None:
            known = sum(1 for k, _ in self._tokens if k == kind)
            # A single one is just "CHILD"; several are told apart by number.
            token = kind if (whole == 1 and not known) else f"{kind} {known + 1}"
            self._tokens[(kind, identity)] = token
            self.mapping[token] = restore
        return token

    # --- masking ----------------------------------------------------------

    def mask_text(self, text):
        """`text` with every name, number and address replaced by its token."""
        text = str(text or "")
        spans = self._spans(text)
        if not spans:
            return text
        distinct = {}
        for _s, _e, kind, identity, _r in spans:
            distinct.setdefault(kind, set()).add(identity)
        out, at = [], 0
        for start, end, kind, identity, restore in spans:
            out.append(text[at:start])
            out.append(self._token(kind, identity, restore,
                                   whole=len(distinct[kind])))
            at = end
        out.append(text[at:])
        return "".join(out)

    # --- putting back -----------------------------------------------------

    def restore(self, value):
        """`value` with each token swapped for what it stood for.

        Strings, and the strings inside dicts and lists; anything else comes
        back as it was. A string that IS a token, in any case, is replaced
        whole - the model sometimes writes "Child" - but a token inside longer
        text only when written exactly, because "child" is also an ordinary
        word in an argument like "child neglect".
        """
        if isinstance(value, dict):
            return {k: self.restore(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.restore(v) for v in value]
        if not isinstance(value, str) or not self.mapping:
            return value
        whole = value.strip().casefold()
        for token, original in self.mapping.items():
            if whole == token.casefold():
                return original
        for token in sorted(self.mapping, key=len, reverse=True):
            value = re.sub(rf"(?<!\w){re.escape(token)}(?!\w)",
                           lambda _m, o=self.mapping[token]: o, value)
        return value


def mask(question, request):
    """(masked question, mapping) for `question` as `request`'s user may see it.

    `mapping` is token -> what to put back: the child's recorded name where
    what was typed names exactly one of the requester's children, else the
    text as it was typed. Nothing the requester cannot see is recognised.
    """
    masker = Masker(request)
    masked = masker.mask_text(question)
    return masked, dict(masker.mapping)
