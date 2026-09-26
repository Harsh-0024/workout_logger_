"""
Build the downloadable Apple Shortcuts (static/shortcuts/*.shortcut).

    python scripts/build_apple_shortcuts.py

Needs macOS: Shortcuts only imports signed files, and signing is done by the
`shortcuts sign` command. The files hold no personal data: when someone adds a
shortcut, Shortcuts asks for their key from Settings > Apple Shortcuts.

Each request goes to Railway first. If the reply isn't this app's JSON (every
shortcut reply has a "server" key), the same request goes to Render.
"""
import copy
import os
import plistlib
import subprocess
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import Config  # noqa: E402

HOSTS = dict(
    entry.strip().rpartition("=")[::2]
    for entry in Config.DEPLOYMENT_URLS.split(",")
    if entry.strip()
)
PRIMARY, FALLBACK = HOSTS["Railway"].rstrip("/"), HOSTS["Render"].rstrip("/")
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "shortcuts")
KEY_QUESTION = "Paste your shortcut key. In Workout Logger, open Settings → Integrations → Apple Shortcuts and tap Copy."
OBJ = "￼"  # placeholder Shortcuts uses for a variable inside text

NOTES_APP = {"TeamIdentifier": "0000000000", "BundleIdentifier": "com.apple.Notes", "Name": "Notes"}


def uid():
    return str(uuid.uuid4()).upper()


def ref(u, name):
    return {"OutputUUID": u, "Type": "ActionOutput", "OutputName": name}


def var(name):
    return {"Type": "Variable", "VariableName": name}


def att(r):
    return {"Value": r, "WFSerializationType": "WFTextTokenAttachment"}


def text(s, *refs):
    """Text with variables: each OBJ in s is filled by the next ref."""
    ranges, pos = {}, 0
    for r in refs:
        pos = s.index(OBJ, pos)
        ranges[f"{{{pos}, 1}}"] = r
        pos += 1
    value = {"string": s}
    if ranges:
        value["attachmentsByRange"] = ranges
    return {"Value": value, "WFSerializationType": "WFTextTokenString"}


def act(ident, **params):
    if not ident.startswith(("is.", "com.")):
        ident = "is.workflow.actions." + ident
    return {"WFWorkflowActionIdentifier": ident, "WFWorkflowActionParameters": params}


class Flow:
    def __init__(self):
        self.actions = []

    def add(self, action):
        self.actions.append(action)

    def out(self, ident, name, **params):
        u = uid()
        self.add(act(ident, UUID=u, **params))
        return ref(u, name)

    def set_var(self, name, r):
        self.add(act("setvariable", WFVariableName=name, WFInput=att(r)))

    def get(self, key):
        return self.out("getvalueforkey", "Dictionary Value", WFDictionaryKey=key, WFInput=att(var("Reply")))

    def begin_if(self, r, condition, **extra):
        g = uid()
        self.add(act("conditional", GroupingIdentifier=g, WFControlFlowMode=0, WFCondition=condition,
                     WFInput={"Type": "Variable", "Variable": att(r)}, **extra))
        return g

    def otherwise(self, g):
        self.add(act("conditional", GroupingIdentifier=g, WFControlFlowMode=1))

    def end_if(self, g):
        self.add(act("conditional", GroupingIdentifier=g, WFControlFlowMode=2, UUID=uid()))

    def stop_with_alert(self, message, title=None):
        params = {"WFAlertActionMessage": message, "WFAlertActionCancelButtonShown": False}
        if title:
            params["WFAlertActionTitle"] = title
        self.add(act("alert", **params))
        self.add(act("exit"))

    def ask_for_key(self):
        """Action 0 holds the pasted key (the import question fills it in).
        A whole Log or Retrieve link also works: only the key part is kept."""
        pasted = self.out("gettext", "Text", WFTextActionText="")
        key = self.out(
            "text.replace", "Updated Text", WFInput=text(OBJ, pasted),
            WFReplaceTextRegularExpression=True,
            WFReplaceTextFind=r"^\s*(?:\S*/shortcut/(?:log|pick)/)?([^/?#\s]+)[\s\S]*$",
            WFReplaceTextReplace="$1",
        )
        self.set_var("Key", key)

    def fetch(self, path, *path_refs, form_text=None):
        """Railway, then Render if Railway's reply isn't ours; stop with an alert if neither answers."""
        def request(base):
            params = {"WFURL": text(f"{base}/shortcut/{path}", var("Key"), *path_refs)}
            if form_text:
                params.update(
                    WFHTTPMethod="POST", WFHTTPBodyType="Form",
                    WFFormValues={"Value": {"WFDictionaryFieldValueItems": [{
                        "WFItemType": 0, "WFKey": text("workout_text"), "WFValue": text(OBJ, form_text),
                    }]}, "WFSerializationType": "WFDictionaryFieldValue"},
                )
            self.set_var("Reply", self.out("downloadurl", "Contents of URL", **params))

        def if_reply_not_ours():
            # Read as plain text: reading an HTML error page as a dictionary would stop the shortcut.
            as_text = self.out("detect.text", "Text", WFInput=att(var("Reply")))
            return self.begin_if(as_text, 999, WFConditionalActionString='"server":')

        request(PRIMARY)
        g = if_reply_not_ours()
        request(FALLBACK)
        self.end_if(g)
        g = if_reply_not_ours()
        self.stop_with_alert("Neither Railway nor Render answered. Try again in a minute.",
                             title="Workout Logger is not reachable")
        self.end_if(g)

    def stop_unless_ok(self):
        ok = self.get("ok")
        g = self.begin_if({**ok, "Aggrandizements": [
            {"Type": "WFCoercionVariableAggrandizement", "CoercionItemClass": "WFBooleanContentItem"}]}, 4)
        self.otherwise(g)
        self.stop_with_alert(text(OBJ, self.get("error")))
        self.end_if(g)


def log_workout():
    f = Flow()
    f.ask_for_key()
    notes = f.out("filter.notes", "Notes", AppIntentDescriptor={**NOTES_APP, "AppIntentIdentifier": "NoteEntity",
                                                               "ActionRequiresAppInstallation": True},
                  WFContentItemSortProperty="Creation Date", WFContentItemSortOrder="Latest First",
                  WFContentItemLimitEnabled=True, WFContentItemLimitNumber=5.0)
    note = f.out("choosefromlist", "Selected Item", WFInput=att(notes),
                 WFChooseFromListActionPrompt="Which note is today's workout?")
    note_text = f.out("detect.text", "Text", WFInput=att(note))
    f.fetch(f"log/{OBJ}", form_text=note_text)
    f.stop_unless_ok()
    f.add(act("openurl", WFInput=att(f.get("result_url"))))
    return f


def get_workout():
    f = Flow()
    f.ask_for_key()
    f.fetch(f"pick/{OBJ}?list=1")
    f.stop_unless_ok()
    session = f.out("choosefromlist", "Selected Item", WFInput=att(f.get("sessions")),
                    WFChooseFromListActionPrompt="Which session?")
    encoded = f.out("urlencode", "URL Encoded Text", WFInput=text(OBJ, session))
    f.fetch(f"pick/{OBJ}?format=json&key={OBJ}", encoded)
    f.stop_unless_ok()
    f.add(act("com.apple.mobilenotes.SharingExtension", UUID=uid(),
              AppIntentDescriptor={**NOTES_APP, "AppIntentIdentifier": "CreateNoteLinkAction"},
              WFCreateNoteInput=text(OBJ, f.get("text"))))
    return f


def save(name, flow, color):
    workflow = {
        "WFWorkflowActions": flow.actions,
        "WFWorkflowClientVersion": "4407",
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowIcon": {"WFWorkflowIconStartColor": color, "WFWorkflowIconGlyphNumber": 61440},
        "WFWorkflowTypes": ["WFWorkflowTypeShowInSearch"],
        "WFWorkflowInputContentItemClasses": [],
        "WFWorkflowOutputContentItemClasses": [],
        "WFWorkflowHasOutputFallback": False,
        "WFWorkflowHasShortcutInputVariables": False,
        "WFQuickActionSurfaces": [],
        "WFWorkflowImportQuestions": [{
            "ActionIndex": 0, "Category": "Parameter", "ParameterKey": "WFTextActionText",
            "DefaultValue": "", "Text": KEY_QUESTION,
        }],
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        unsigned = os.path.join(tmp, f"{name}.wflow")
        with open(unsigned, "wb") as fh:
            plistlib.dump(copy.deepcopy(workflow), fh, fmt=plistlib.FMT_BINARY)
        target = os.path.join(OUT_DIR, f"{name}.shortcut")
        subprocess.run(["shortcuts", "sign", "--mode", "anyone", "--input", unsigned, "--output", target], check=True)
    print("wrote", os.path.relpath(target))


if __name__ == "__main__":
    save("Log Workout", log_workout(), 4282601983)
    save("Get Workout", get_workout(), 2071128575)
