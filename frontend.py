import os
from datetime import datetime
import requests
import streamlit as st
try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")

st.set_page_config(page_title="Land Records Registry", page_icon="🗺️", layout="wide")

# Native widget colors (inputs, buttons, dataframes, date pickers, sliders) come
# from .streamlit/config.toml's [theme] block -- that's what keeps every widget
# readable on dark, not just the elements this CSS touches by hand. This CSS
# layer only handles branding, cards, badges, and a few contrast touch-ups.
CUSTOM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&family=Noto+Sans+Devanagari:wght@400;500;600;700&family=Noto+Sans+Telugu:wght@400;500;600;700&family=Noto+Sans+Tamil:wght@400;500;600;700&family=Noto+Sans+Gurmukhi:wght@400;500;600;700&family=Noto+Sans+Malayalam:wght@400;500;600;700&display=swap');

:root {
    /* National-portal-of-India inspired palette: navy header/nav, white
       content, saffron accent for primary actions, green for success --
       the tricolor, used the way most Indian government portals use it
       (a thin saffron/white/green band, navy chrome, not literal flag art). */
    --rr-bg: #F2F4F7;
    --rr-bg-raised: #0B3866;
    --rr-panel: #FFFFFF;
    --rr-panel-hover: #F5F8FC;
    --rr-border: #D7DEE8;
    --rr-text: #1E2733;
    --rr-heading: #0B3866;
    --rr-muted: #5C6B7F;
    --rr-accent: #FF9933;
    --rr-accent-dark: #E07E14;
    --rr-accent-soft: rgba(255, 153, 51, 0.16);
    --rr-green: #128807;
    --rr-green-bg: rgba(18, 136, 7, 0.12);
    --rr-rust: #D32F2F;
    --rr-rust-bg: rgba(211, 47, 47, 0.12);
    --rr-amber: #B8860B;
    --rr-amber-bg: rgba(184, 134, 11, 0.14);
    --rr-navy: #0B3866;
    --rr-navy-deep: #072745;
    --rr-saffron: #FF9933;
    --rr-tricolor-green: #128807;
}

/* Tricolor band across the very top of the page, like the banner strip
   many Indian government portals use above the header bar. */
.rr-tricolor-strip {
    position: fixed; top: 0; left: 0; right: 0; height: 5px; z-index: 999999;
    background: linear-gradient(to right, var(--rr-saffron) 0 33.3%, #FFFFFF 33.3% 66.6%, var(--rr-tricolor-green) 66.6% 100%);
}

html, body, [data-testid="stAppViewContainer"] {
    background-color: var(--rr-bg);
    color: var(--rr-text);
    font-family: 'Inter', 'Noto Sans Devanagari', 'Noto Sans Telugu', 'Noto Sans Tamil', 'Noto Sans Gurmukhi', 'Noto Sans Malayalam', -apple-system, sans-serif;
}
[data-testid="stHeader"] { background: var(--rr-navy) !important; top: 5px; }
[data-testid="stDecoration"] { display: none; }
#MainMenu { visibility: hidden; }
footer { visibility: hidden; }
[data-testid="stAppViewContainer"] { padding-top: 5px; }

h1, h2, h3 {
    font-family: 'Inter', 'Noto Sans Devanagari', 'Noto Sans Telugu', 'Noto Sans Tamil', 'Noto Sans Gurmukhi', 'Noto Sans Malayalam', -apple-system, sans-serif !important;
    color: var(--rr-heading) !important;
    font-weight: 700 !important;
    letter-spacing: -0.01em;
}
p, span, label, li { color: var(--rr-text); line-height: 1.55; }
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] * { color: var(--rr-muted) !important; }

/* Nav buttons in the sidebar (My Land Records, Citizen Submissions, etc.) --
   full width, left-aligned, and allowed to wrap onto a second line so longer
   translated labels (Punjabi, Tamil, ...) don't get clipped or squeezed. */
[data-testid="stSidebar"] [data-testid="stButton"] button {
    white-space: normal; text-align: left; justify-content: flex-start;
    line-height: 1.35; padding: 10px 14px; margin-bottom: 2px;
}
[data-testid="stSidebar"] [data-testid="stButton"] button[kind="primary"] {
    background-color: var(--rr-accent) !important; border-color: var(--rr-accent-dark) !important; color: #FFFFFF !important;
}
[data-testid="stSidebar"] [data-testid="stButton"] button[kind="secondary"] {
    background-color: transparent !important;
}
[data-testid="stSidebar"] [data-testid="stButton"] button[kind="secondary"]:hover {
    background-color: rgba(255,255,255,0.08) !important; border-color: rgba(255,255,255,0.25) !important;
}

/* Account avatar button that opens the identity/language/logout popover */
[data-testid="stSidebar"] [data-testid="stPopover"] > div > button {
    border-radius: 999px !important; aspect-ratio: 1 / 1; padding: 0 !important;
    font-weight: 700; background-color: var(--rr-accent) !important;
    border-color: var(--rr-accent-dark) !important; color: #FFFFFF !important;
}

/* Sidebar */
[data-testid="stSidebar"] { background-color: var(--rr-bg-raised) !important; border-right: 1px solid var(--rr-border); }
[data-testid="stSidebar"] > div, [data-testid="stSidebarContent"], [data-testid="stSidebarUserContent"] { background-color: transparent !important; }
[data-testid="stSidebar"] * { color: #F4F6FB !important; opacity: 1 !important; }
[data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 { color: #FFFFFF !important; }
[data-testid="stSidebar"] hr { border-color: rgba(255,255,255,0.12); }
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] { color: #8E9BBA !important; }
[data-testid="stSidebar"] [data-testid="stButton"] button {
    background-color: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.18); color: #FFFFFF !important;
}
[data-testid="stSidebar"] [data-testid="stButton"] button:hover { background-color: var(--rr-accent); border-color: var(--rr-accent); color: #FFFFFF !important; }
[data-testid="stSidebar"] [data-baseweb="slider"] [role="slider"] { background-color: var(--rr-accent) !important; }
[data-testid="stSidebar"] [data-testid="stTickBar"] { background: rgba(255,255,255,0.12) !important; }
[data-testid="stSidebar"] [data-baseweb="select"] > div { background-color: rgba(255,255,255,0.05) !important; border-color: rgba(255,255,255,0.18) !important; }

/* IMPORTANT: the generic "bordered container" rule further below paints a
   white card behind anything Streamlit wraps in stVerticalBlockBorderWrapper
   -- including, in current Streamlit versions, the sidebar's own content
   block. Left unscoped, that white card sits behind the nav buttons and
   makes their light-on-dark text unreadable. This more specific selector
   (two attribute selectors, so it wins the cascade) forces those wrappers
   back to transparent whenever they appear inside the sidebar. */
[data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"] {
    background-color: transparent !important; border-color: rgba(255,255,255,0.14) !important; box-shadow: none !important;
}

/* Buttons -- always visibly outlined against the light background, even
   when Streamlit's own theme doesn't draw a border by default. */
[data-testid="stButton"] button, [data-testid="stFormSubmitButton"] button {
    border-radius: 6px; font-family: 'Inter', 'Noto Sans Devanagari', 'Noto Sans Telugu', 'Noto Sans Tamil', 'Noto Sans Gurmukhi', 'Noto Sans Malayalam', sans-serif; font-weight: 600;
    border: 1.5px solid var(--rr-border);
}
[data-testid="stButton"] button {
    background-color: #FFFFFF; color: var(--rr-heading);
}
[data-testid="stButton"] button:hover {
    border-color: var(--rr-accent); color: var(--rr-accent-dark); background-color: var(--rr-accent-soft);
}
[data-testid="stFormSubmitButton"] button {
    background-color: var(--rr-accent); color: #FFFFFF; border: 1.5px solid var(--rr-accent-dark);
}
[data-testid="stFormSubmitButton"] button:hover { background-color: var(--rr-accent-dark); border-color: var(--rr-accent-dark); }

/* Inputs, selects, text areas, date pickers -- a clear, visible outline on
   every field so it reads as an editable box against the white/light-grey
   background, not just a bare line. */
[data-testid="stTextInput"] input,
[data-testid="stNumberInput"] input,
[data-testid="stTextArea"] textarea,
[data-testid="stDateInput"] input,
[data-baseweb="input"] > div,
[data-baseweb="base-input"],
[data-baseweb="select"] > div,
[data-baseweb="textarea"] {
    border: 1.5px solid var(--rr-border) !important;
    background-color: #FFFFFF !important;
    border-radius: 6px !important;
}
[data-testid="stTextInput"] input:focus,
[data-testid="stNumberInput"] input:focus,
[data-testid="stTextArea"] textarea:focus,
[data-baseweb="input"]:focus-within > div,
[data-baseweb="select"]:focus-within > div {
    border-color: var(--rr-accent) !important;
    box-shadow: 0 0 0 1px var(--rr-accent) !important;
}

/* File uploader -- clear dashed outline around the drop zone */
[data-testid="stFileUploaderDropzone"] {
    border: 1.5px dashed var(--rr-border) !important;
    background-color: #FFFFFF !important;
    border-radius: 8px !important;
}
[data-testid="stFileUploaderDropzone"]:hover { border-color: var(--rr-accent) !important; }

/* Dataframes / tables -- give the whole grid a visible frame */
[data-testid="stDataFrame"], [data-testid="stTable"] {
    border: 1px solid var(--rr-border) !important; border-radius: 6px; overflow: hidden;
}

/* Expanders -- outline the collapsible box so it reads as a distinct unit */
[data-testid="stExpander"] {
    border: 1px solid var(--rr-border) !important; border-radius: 8px !important; background-color: var(--rr-panel);
}

/* Checkboxes / toggles / radio -- make the control itself clearly outlined */
[data-testid="stCheckbox"] label span[aria-checked],
[data-baseweb="checkbox"] span:first-child,
[data-baseweb="radio"] span:first-child {
    border: 1.5px solid var(--rr-border) !important;
}

/* Tabs -- clean underline style */
[data-baseweb="tab-list"] { gap: 4px; border-bottom: 1px solid var(--rr-border); }
[data-baseweb="tab"] { font-family: 'Inter', 'Noto Sans Devanagari', 'Noto Sans Telugu', 'Noto Sans Tamil', 'Noto Sans Gurmukhi', 'Noto Sans Malayalam', sans-serif; font-weight: 600; color: var(--rr-muted); }
[data-baseweb="tab"] p { color: inherit !important; }
[data-baseweb="tab"][aria-selected="true"] { color: var(--rr-heading); }
[data-baseweb="tab"][aria-selected="true"] p { color: var(--rr-heading) !important; }
[data-baseweb="tab-highlight"] { background-color: var(--rr-accent) !important; height: 3px; }

/* Bordered containers -- clean card look */
[data-testid="stVerticalBlockBorderWrapper"] {
    border-radius: 10px !important; border: 1px solid var(--rr-border) !important; background-color: var(--rr-panel);
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.35);
}

/* Metrics and identifiers -- monospace so numbers actually line up */
[data-testid="stMetricValue"] { font-family: 'IBM Plex Mono', monospace; color: var(--rr-heading); font-weight: 700; }
[data-testid="stMetricLabel"] { color: var(--rr-muted) !important; }
.rr-mono { font-family: 'IBM Plex Mono', monospace; }

hr { border-color: var(--rr-border); }

/* Status tags -- flat, modern pill badges (readable on dark backgrounds) */
.rr-badge {
    display: inline-flex; align-items: center; gap: 6px; padding: 3px 11px; border-radius: 999px;
    font-size: 0.8rem; font-weight: 600; font-family: 'Inter', 'Noto Sans Devanagari', 'Noto Sans Telugu', 'Noto Sans Tamil', 'Noto Sans Gurmukhi', 'Noto Sans Malayalam', sans-serif;
}
.rr-badge--pending { background: var(--rr-amber-bg); color: var(--rr-amber); }
.rr-badge--approved { background: var(--rr-green-bg); color: var(--rr-green); }
.rr-badge--rejected { background: var(--rr-rust-bg); color: var(--rr-rust); }
.rr-badge--online { background: var(--rr-green-bg); color: var(--rr-green); }
.rr-badge--offline { background: rgba(255,255,255,0.08); color: var(--rr-muted); }

.rr-dot { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:6px; }
.rr-dot--online { background: var(--rr-green); }
.rr-dot--offline { background: var(--rr-rust); }

/* Sidebar brand + identity card */
.rr-brand { display:flex; align-items:center; gap:10px; margin-bottom: 14px; }
.rr-brand-mark { font-size: 1.5rem; }
.rr-brand-word { font-family: 'Inter', 'Noto Sans Devanagari', 'Noto Sans Telugu', 'Noto Sans Tamil', 'Noto Sans Gurmukhi', 'Noto Sans Malayalam', sans-serif; font-size: 1.15rem; font-weight: 700; color: #FFFFFF; line-height: 1.3; }
.rr-identity-card { background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.14); border-radius: 8px; padding: 12px 14px; margin-bottom: 10px; }
.rr-identity-name { font-weight: 700; font-size: 0.98rem; color: #FFFFFF; }
.rr-identity-phone { font-family: 'IBM Plex Mono', monospace; font-size: 0.8rem; color: #B7C1DA; }
.rr-tag {
    display:inline-block; font-size: 0.72rem; font-weight: 600; padding: 2px 9px; border-radius: 999px;
    margin-top: 8px; margin-right: 5px; background: rgba(255,153,51,0.28); color: #FFE7C7; border: 1px solid rgba(255,153,51,0.55);
}

/* Password strength hint under password fields */
.rr-pw-hint { font-size: 0.82rem; color: var(--rr-muted); margin-top: -6px; margin-bottom: 6px; }
.rr-pw-hint--ok { color: var(--rr-green); }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
st.markdown('<div class="rr-tricolor-strip"></div>', unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Language support
# ---------------------------------------------------------------------------

LANGUAGE_NAMES = {
    "en": "English",
    "hi": "हिन्दी",
    "te": "తెలుగు",
    "ta": "தமிழ்",
    "pa": "ਪੰਜਾਬੀ",
    "ml": "മലയാളം",
}

TRANSLATIONS = {
    "app_name": {"en": "Land Records Registry", "hi": "भूमि अभिलेख रजिस्ट्री", "te": "భూమి రికార్డుల రిజిస్ట్రీ", "ta": "நில பதிவு பதிவகம்", "pa": "ਜ਼ਮੀਨ ਰਿਕਾਰਡ ਰਜਿਸਟਰੀ", "ml": "ഭൂരേഖാ രജിസ്ട്രി"},
    "login_subtitle": {"en": "Log in with your phone number to view land parcels registered in your name.", "hi": "अपने नाम पर पंजीकृत भूखंड देखने के लिए अपने फ़ोन नंबर से लॉग इन करें।", "te": "మీ పేరిట నమోదైన భూ ప్లాట్లను చూడటానికి మీ ఫోన్ నంబర్‌తో లాగిన్ అవ్వండి.", "ta": "உங்கள் பெயரில் பதிவு செய்யப்பட்ட நில பகுதிகளைப் பார்க்க உங்கள் தொலைபேசி எண்ணுடன் உள்நுழையவும்.", "pa": "ਆਪਣੇ ਨਾਮ 'ਤੇ ਦਰਜ ਜ਼ਮੀਨ ਦੇ ਟੁਕੜੇ ਵੇਖਣ ਲਈ ਆਪਣੇ ਫ਼ੋਨ ਨੰਬਰ ਨਾਲ ਲੌਗ ਇਨ ਕਰੋ।", "ml": "നിങ്ങളുടെ പേരിൽ രജിസ്റ്റർ ചെയ്ത ഭൂഖണ്ഡങ്ങൾ കാണാൻ നിങ്ങളുടെ ഫോൺ നമ്പർ ഉപയോഗിച്ച് ലോഗിൻ ചെയ്യുക."},
    "language_label": {"en": "Language", "hi": "भाषा", "te": "భాష", "ta": "மொழி", "pa": "ਭਾਸ਼ਾ", "ml": "ഭാഷ"},
    "phone_label": {"en": "Phone Number", "hi": "फ़ोन नंबर", "te": "ఫోన్ నంబర్", "ta": "தொலைபேசி எண்", "pa": "ਫ਼ੋਨ ਨੰਬਰ", "ml": "ഫോൺ നമ്പർ"},
    "phone_placeholder": {"en": "10-digit mobile number", "hi": "10 अंकों का मोबाइल नंबर", "te": "10 అంకెల మొబైల్ నంబర్", "ta": "10 இலக்க மொபைல் எண்", "pa": "10 ਅੰਕਾਂ ਦਾ ਮੋਬਾਈਲ ਨੰਬਰ", "ml": "10 അക്ക മൊബൈൽ നമ്പർ"},
    "continue_btn": {"en": "Continue", "hi": "जारी रखें", "te": "కొనసాగించు", "ta": "தொடரவும்", "pa": "ਜਾਰੀ ਰੱਖੋ", "ml": "തുടരുക"},
    "not_registered_notice": {"en": "**{phone}** isn't registered yet. Create an account below.", "hi": "**{phone}** अभी पंजीकृत नहीं है। नीचे खाता बनाएं।", "te": "**{phone}** ఇంకా నమోదు కాలేదు. దిగువ ఖాతాను సృష్టించండి.", "ta": "**{phone}** இன்னும் பதிவு செய்யப்படவில்லை. கீழே கணக்கை உருவாக்கவும்.", "pa": "**{phone}** ਅਜੇ ਰਜਿਸਟਰਡ ਨਹੀਂ ਹੈ। ਹੇਠਾਂ ਖਾਤਾ ਬਣਾਓ।", "ml": "**{phone}** ഇതുവരെ രജിസ്റ്റർ ചെയ്തിട്ടില്ല. താഴെ ഒരു അക്കൗണ്ട് ഉണ്ടാക്കുക."},
    "full_name_label": {"en": "Full Name", "hi": "पूरा नाम", "te": "పూర్తి పేరు", "ta": "முழுப்பெயர்", "pa": "ਪੂਰਾ ਨਾਮ", "ml": "മുഴുവൻ പേര്"},
    "name_help": {"en": "Choose this carefully — it's how you'll be identified on every land record, and can't be changed later from here.", "hi": "इसे ध्यान से चुनें — हर भूमि रिकॉर्ड पर आपकी यही पहचान होगी, और इसे यहां से बाद में बदला नहीं जा सकता।", "te": "దీన్ని జాగ్రత్తగా ఎంచుకోండి — ప్రతి భూ రికార్డులో మీ గుర్తింపు ఇదే, దీన్ని తర్వాత ఇక్కడి నుండి మార్చలేరు.", "ta": "இதை கவனமாகத் தேர்வு செய்யவும் — ஒவ்வொரு நில பதிவிலும் இதுவே உங்கள் அடையாளம், இதை பின்னர் இங்கிருந்து மாற்ற முடியாது.", "pa": "ਇਸਨੂੰ ਧਿਆਨ ਨਾਲ ਚੁਣੋ — ਹਰ ਜ਼ਮੀਨ ਰਿਕਾਰਡ 'ਤੇ ਤੁਹਾਡੀ ਇਹੀ ਪਛਾਣ ਹੋਵੇਗੀ, ਅਤੇ ਇਸਨੂੰ ਬਾਅਦ ਵਿੱਚ ਇੱਥੋਂ ਬਦਲਿਆ ਨਹੀਂ ਜਾ ਸਕਦਾ।", "ml": "ഇത് ശ്രദ്ധയോടെ തിരഞ്ഞെടുക്കുക — എല്ലാ ഭൂരേഖയിലും നിങ്ങളെ തിരിച്ചറിയുന്നത് ഇതുവഴിയാണ്, ഇത് പിന്നീട് ഇവിടെ നിന്ന് മാറ്റാൻ കഴിയില്ല."},
    "choose_password_label": {"en": "Choose a Password", "hi": "एक पासवर्ड चुनें", "te": "పాస్‌వర్డ్‌ను ఎంచుకోండి", "ta": "கடவுச்சொல்லைத் தேர்வுசெய்க", "pa": "ਇੱਕ ਪਾਸਵਰਡ ਚੁਣੋ", "ml": "ഒരു പാസ്‌വേഡ് തിരഞ്ഞെടുക്കുക"},
    "confirm_password_label": {"en": "Confirm Password", "hi": "पासवर्ड की पुष्टि करें", "te": "పాస్‌వర్డ్‌ను నిర్ధారించండి", "ta": "கடவுச்சொல்லை உறுதிப்படுத்தவும்", "pa": "ਪਾਸਵਰਡ ਦੀ ਪੁਸ਼ਟੀ ਕਰੋ", "ml": "പാസ്‌വേഡ് സ്ഥിരീകരിക്കുക"},
    "password_requirements": {"en": "At least 8 characters, with an uppercase letter, a number, and a special character.", "hi": "कम से कम 8 अक्षर, जिसमें एक बड़ा अक्षर, एक अंक और एक विशेष चिह्न हो।", "te": "కనీసం 8 అక్షరాలు, ఒక పెద్ద అక్షరం, ఒక అంకె మరియు ఒక ప్రత్యేక చిహ్నంతో ఉండాలి.", "ta": "குறைந்தது 8 எழுத்துகள், ஒரு பெரிய எழுத்து, ஒரு எண் மற்றும் ஒரு சிறப்பு எழுத்துடன் இருக்க வேண்டும்.", "pa": "ਘੱਟੋ-ਘੱਟ 8 ਅੱਖਰ, ਇੱਕ ਵੱਡਾ ਅੱਖਰ, ਇੱਕ ਅੰਕ ਅਤੇ ਇੱਕ ਖ਼ਾਸ ਚਿੰਨ੍ਹ ਦੇ ਨਾਲ।", "ml": "കുറഞ്ഞത് 8 പ്രതീകങ്ങൾ, ഒരു വലിയ അക്ഷരം, ഒരു അക്കം, ഒരു പ്രത്യേക ചിഹ്നം എന്നിവ ഉണ്ടായിരിക്കണം."},
    "create_account_btn": {"en": "Create Account", "hi": "खाता बनाएं", "te": "ఖాతాను సృష్టించండి", "ta": "கணக்கை உருவாக்கு", "pa": "ਖਾਤਾ ਬਣਾਓ", "ml": "അക്കൗണ്ട് ഉണ്ടാക്കുക"},
    "use_different_number_btn": {"en": "Use a different number", "hi": "अलग नंबर इस्तेमाल करें", "te": "వేరే నంబర్‌ను ఉపయోగించండి", "ta": "வேறு எண்ணைப் பயன்படுத்தவும்", "pa": "ਵੱਖਰਾ ਨੰਬਰ ਵਰਤੋ", "ml": "മറ്റൊരു നമ്പർ ഉപയോഗിക്കുക"},
    "set_password_notice": {"en": "Welcome back. **{phone}** needs a password set up before you can continue.", "hi": "वापसी पर स्वागत है। जारी रखने से पहले **{phone}** के लिए पासवर्ड सेट करना ज़रूरी है।", "te": "తిరిగి స్వాగతం. కొనసాగించే ముందు **{phone}** కోసం పాస్‌వర్డ్ సెట్ చేయాలి.", "ta": "மீண்டும் வருக. தொடர்வதற்கு முன் **{phone}** க்கு கடவுச்சொல் அமைக்க வேண்டும்.", "pa": "ਵਾਪਸ ਸਵਾਗਤ ਹੈ। ਜਾਰੀ ਰੱਖਣ ਤੋਂ ਪਹਿਲਾਂ **{phone}** ਲਈ ਪਾਸਵਰਡ ਸੈੱਟ ਕਰਨਾ ਜ਼ਰੂਰੀ ਹੈ।", "ml": "വീണ്ടും സ്വാഗതം. തുടരുന്നതിന് മുൻപ് **{phone}**-ന് ഒരു പാസ്‌വേഡ് സജ്ജമാക്കണം."},
    "set_password_btn": {"en": "Set Password & Log In", "hi": "पासवर्ड सेट करें और लॉग इन करें", "te": "పాస్‌వర్డ్ సెట్ చేసి లాగిన్ అవ్వండి", "ta": "கடவுச்சொல்லை அமைத்து உள்நுழையவும்", "pa": "ਪਾਸਵਰਡ ਸੈੱਟ ਕਰੋ ਅਤੇ ਲੌਗ ਇਨ ਕਰੋ", "ml": "പാസ്‌വേഡ് സജ്ജമാക്കി ലോഗിൻ ചെയ്യുക"},
    "login_as_caption": {"en": "Logging in as **{phone}**", "hi": "**{phone}** के रूप में लॉग इन हो रहा है", "te": "**{phone}** గా లాగిన్ అవుతోంది", "ta": "**{phone}** ஆக உள்நுழைகிறது", "pa": "**{phone}** ਵਜੋਂ ਲੌਗ ਇਨ ਹੋ ਰਿਹਾ ਹੈ", "ml": "**{phone}** ആയി ലോഗിൻ ചെയ്യുന്നു"},
    "password_label": {"en": "Password", "hi": "पासवर्ड", "te": "పాస్‌వర్డ్", "ta": "கடவுச்சொல்", "pa": "ਪਾਸਵਰਡ", "ml": "പാസ്‌വേഡ്"},
    "login_btn": {"en": "Log In", "hi": "लॉग इन करें", "te": "లాగిన్", "ta": "உள்நுழை", "pa": "ਲੌਗ ਇਨ ਕਰੋ", "ml": "ലോഗിൻ"},
    "logout_btn": {"en": "Log out", "hi": "लॉग आउट", "te": "లాగ్ అవుట్", "ta": "வெளியேறு", "pa": "ਲੌਗ ਆਉਟ", "ml": "ലോഗൗട്ട്"},
    "live_updates_label": {"en": "Live updates", "hi": "लाइव अपडेट", "te": "లైవ్ అప్‌డేట్‌లు", "ta": "நேரடி புதுப்பிப்புகள்", "pa": "ਲਾਈਵ ਅੱਪਡੇਟ", "ml": "തത്സമയ അപ്‌ഡേറ്റുകൾ"},
    "refresh_every_label": {"en": "Refresh every", "hi": "हर बार रिफ्रेश करें", "te": "ప్రతిసారీ రిఫ్రెష్ చేయండి", "ta": "ஒவ்வொரு முறையும் புதுப்பிக்கவும்", "pa": "ਹਰ ਵਾਰ ਤਾਜ਼ਾ ਕਰੋ", "ml": "ഓരോ തവണയും പുതുക്കുക"},
    "settings_label": {"en": "Settings", "hi": "सेटिंग्स", "te": "సెట్టింగ్‌లు", "ta": "அமைப்புகள்", "pa": "ਸੈਟਿੰਗਾਂ", "ml": "ക്രമീകരണങ്ങൾ"},
    "tab_my_records": {"en": "My Land Records", "hi": "मेरे भूमि अभिलेख", "te": "నా భూమి రికార్డులు", "ta": "எனது நில பதிவுகள்", "pa": "ਮੇਰੇ ਜ਼ਮੀਨ ਰਿਕਾਰਡ", "ml": "എന്റെ ഭൂരേഖകൾ"},
    "tab_submit_document": {"en": "Submit a Document", "hi": "दस्तावेज़ जमा करें", "te": "పత్రాన్ని సమర్పించండి", "ta": "ஆவணத்தை சமர்ப்பிக்கவும்", "pa": "ਦਸਤਾਵੇਜ਼ ਜਮ੍ਹਾਂ ਕਰੋ", "ml": "ഒരു രേഖ സമർപ്പിക്കുക"},
    "tab_citizen_submissions": {"en": "Citizen Submissions", "hi": "नागरिक प्रस्तुतियाँ", "te": "పౌరుల సమర్పణలు", "ta": "குடிமக்கள் சமர்ப்பணங்கள்", "pa": "ਨਾਗਰਿਕ ਜਮ੍ਹਾਂਕਰਨ", "ml": "പൗര സമർപ്പണങ്ങൾ"},
    "tab_ingest": {"en": "Ingest New Document", "hi": "नया दस्तावेज़ जोड़ें", "te": "కొత్త పత్రాన్ని జోడించండి", "ta": "புதிய ஆவணத்தைச் சேர்க்கவும்", "pa": "ਨਵਾਂ ਦਸਤਾਵੇਜ਼ ਸ਼ਾਮਲ ਕਰੋ", "ml": "പുതിയ രേഖ ചേർക്കുക"},
    "tab_review_queue": {"en": "Review Queue", "hi": "समीक्षा कतार", "te": "సమీక్ష క్యూ", "ta": "மதிப்பாய்வு வரிசை", "pa": "ਸਮੀਖਿਆ ਕਤਾਰ", "ml": "അവലോകന ക്യൂ"},
    "tab_full_registry": {"en": "Full Registry", "hi": "पूर्ण रजिस्ट्री", "te": "పూర్తి రిజిస్ట్రీ", "ta": "முழு பதிவேடு", "pa": "ਪੂਰੀ ਰਜਿਸਟਰੀ", "ml": "പൂർണ്ണ രജിസ്ട്രി"},
    "tab_staff_attendance": {"en": "Staff Attendance", "hi": "स्टाफ़ उपस्थिति", "te": "సిబ్బంది హాజరు", "ta": "பணியாளர் வருகை", "pa": "ਸਟਾਫ਼ ਹਾਜ਼ਰੀ", "ml": "സ്റ്റാഫ് ഹാജർ"},
    "tab_staff_progress": {"en": "Staff Progress", "hi": "स्टाफ़ प्रगति", "te": "సిబ్బంది పురోగతి", "ta": "பணியாளர் முன்னேற்றம்", "pa": "ਸਟਾਫ਼ ਤਰੱਕੀ", "ml": "സ്റ്റാഫ് പുരോഗതി"},
    "tab_manage_staff": {"en": "Manage Staff", "hi": "स्टाफ़ प्रबंधित करें", "te": "సిబ్బందిని నిర్వహించండి", "ta": "பணியாளர்களை நிர்வகிக்கவும்", "pa": "ਸਟਾਫ਼ ਦਾ ਪ੍ਰਬੰਧਨ ਕਰੋ", "ml": "സ്റ്റാഫിനെ നിയന്ത്രിക്കുക"},
    "header_my_records": {"en": "📄 My Land Records", "hi": "📄 मेरे भूमि अभिलेख", "te": "📄 నా భూమి రికార్డులు", "ta": "📄 எனது நில பதிவுகள்", "pa": "📄 ਮੇਰੇ ਜ਼ਮੀਨ ਰਿਕਾਰਡ", "ml": "📄 എന്റെ ഭൂരേഖകൾ"},
    "no_parcels_msg": {"en": "No land parcels are currently registered under your phone number. If this looks wrong, contact registry staff.", "hi": "आपके फ़ोन नंबर के तहत फ़िलहाल कोई भूखंड पंजीकृत नहीं है। यदि यह गलत लगे तो रजिस्ट्री स्टाफ़ से संपर्क करें।", "te": "మీ ఫోన్ నంబర్ కింద ప్రస్తుతం ఏ భూ ప్లాట్లు నమోదు కాలేదు. ఇది తప్పుగా అనిపిస్తే రిజిస్ట్రీ సిబ్బందిని సంప్రదించండి.", "ta": "உங்கள் தொலைபேசி எண்ணின் கீழ் தற்போது எந்த நில பகுதியும் பதிவு செய்யப்படவில்லை. இது தவறாகத் தோன்றினால் பதிவக ஊழியரைத் தொடர்பு கொள்ளவும்.", "pa": "ਤੁਹਾਡੇ ਫ਼ੋਨ ਨੰਬਰ ਹੇਠ ਇਸ ਵੇਲੇ ਕੋਈ ਜ਼ਮੀਨ ਦਾ ਟੁਕੜਾ ਦਰਜ ਨਹੀਂ ਹੈ। ਜੇ ਇਹ ਗਲਤ ਲੱਗੇ ਤਾਂ ਰਜਿਸਟਰੀ ਸਟਾਫ਼ ਨਾਲ ਸੰਪਰਕ ਕਰੋ।", "ml": "നിങ്ങളുടെ ഫോൺ നമ്പറിന് കീഴിൽ നിലവിൽ ഭൂഖണ്ഡങ്ങളൊന്നും രജിസ്റ്റർ ചെയ്തിട്ടില്ല. ഇത് തെറ്റാണെന്ന് തോന്നിയാൽ രജിസ്ട്രി സ്റ്റാഫിനെ ബന്ധപ്പെടുക."},
    "download_doc_btn": {"en": "Download original document", "hi": "मूल दस्तावेज़ डाउनलोड करें", "te": "అసలు పత్రాన్ని డౌన్‌లోడ్ చేయండి", "ta": "மூல ஆவணத்தைப் பதிவிறக்கவும்", "pa": "ਮੂਲ ਦਸਤਾਵੇਜ਼ ਡਾਊਨਲੋਡ ਕਰੋ", "ml": "യഥാർത്ഥ രേഖ ഡൗൺലോഡ് ചെയ്യുക"},
    "header_submit_document": {"en": "📤 Submit a Land Document", "hi": "📤 भूमि दस्तावेज़ जमा करें", "te": "📤 భూమి పత్రాన్ని సమర్పించండి", "ta": "📤 நில ஆவணத்தை சமர்ப்பிக்கவும்", "pa": "📤 ਜ਼ਮੀਨ ਦਾ ਦਸਤਾਵੇਜ਼ ਜਮ੍ਹਾਂ ਕਰੋ", "ml": "📤 ഭൂരേഖ സമർപ്പിക്കുക"},
    "submit_document_caption": {"en": "Upload a photo or scan of your land document. Registry staff will review it and approve the record before it appears under 'My Land Records'.", "hi": "अपने भूमि दस्तावेज़ की फ़ोटो या स्कैन अपलोड करें। रजिस्ट्री स्टाफ़ इसकी समीक्षा करेगा और 'मेरे भूमि अभिलेख' में दिखने से पहले रिकॉर्ड को मंज़ूरी देगा।", "te": "మీ భూమి పత్రం యొక్క ఫోటో లేదా స్కాన్‌ను అప్‌లోడ్ చేయండి. ఇది 'నా భూమి రికార్డులు' లో కనిపించే ముందు రిజిస్ట్రీ సిబ్బంది సమీక్షించి ఆమోదిస్తారు.", "ta": "உங்கள் நில ஆவணத்தின் புகைப்படம் அல்லது ஸ்கேனை பதிவேற்றவும். இது 'எனது நில பதிவுகள்' இல் தோன்றுவதற்கு முன் பதிவக ஊழியர் மதிப்பாய்வு செய்து அங்கீகரிப்பார்.", "pa": "ਆਪਣੇ ਜ਼ਮੀਨ ਦਸਤਾਵੇਜ਼ ਦੀ ਫੋਟੋ ਜਾਂ ਸਕੈਨ ਅੱਪਲੋਡ ਕਰੋ। ਇਹ 'ਮੇਰੇ ਜ਼ਮੀਨ ਰਿਕਾਰਡ' ਵਿੱਚ ਦਿਖਣ ਤੋਂ ਪਹਿਲਾਂ ਰਜਿਸਟਰੀ ਸਟਾਫ਼ ਇਸ ਦੀ ਸਮੀਖਿਆ ਕਰਕੇ ਮਨਜ਼ੂਰੀ ਦੇਵੇਗਾ।", "ml": "നിങ്ങളുടെ ഭൂരേഖയുടെ ഫോട്ടോ അല്ലെങ്കിൽ സ്കാൻ അപ്‌ലോഡ് ചെയ്യുക. 'എന്റെ ഭൂരേഖകൾ' -ൽ കാണിക്കുന്നതിന് മുൻപ് സ്റ്റാഫ് ഇത് അവലോകനം ചെയ്ത് അംഗീകരിക്കും."},
    "upload_document_label": {"en": "Upload document", "hi": "दस्तावेज़ अपलोड करें", "te": "పత్రాన్ని అప్‌లోడ్ చేయండి", "ta": "ஆவணத்தை பதிவேற்றவும்", "pa": "ਦਸਤਾਵੇਜ਼ ਅੱਪਲੋਡ ਕਰੋ", "ml": "രേഖ അപ്‌ലോഡ് ചെയ്യുക"},
    "survey_no_label": {"en": "Survey / Khasra / Gat Number (if you know it)", "hi": "सर्वे / खसरा / गट नंबर (यदि पता हो)", "te": "సర్వే / ఖస్రా / గట్ నంబర్ (తెలిస్తే)", "ta": "சர்வே / கசரா / கத் எண் (தெரிந்தால்)", "pa": "ਸਰਵੇ / ਖਸਰਾ / ਗਟ ਨੰਬਰ (ਜੇ ਪਤਾ ਹੋਵੇ)", "ml": "സർവേ / ഖസ്ര / ഗട്ട് നമ്പർ (അറിയാമെങ്കിൽ)"},
    "staff_note_label": {"en": "Anything staff should know (optional)", "hi": "स्टाफ़ को कुछ बताना हो (वैकल्पिक)", "te": "సిబ్బంది తెలుసుకోవలసినది ఏదైనా ఉంటే (ఐచ్ఛికం)", "ta": "ஊழியர் அறிய வேண்டியது ஏதேனும் (விருப்பம்)", "pa": "ਸਟਾਫ਼ ਨੂੰ ਕੁਝ ਦੱਸਣਾ ਹੋਵੇ (ਵਿਕਲਪਿਕ)", "ml": "സ്റ്റാഫ് അറിഞ്ഞിരിക്കേണ്ട എന്തെങ്കിലും (ഐച്ഛികം)"},
    "submit_review_btn": {"en": "Submit for Review", "hi": "समीक्षा के लिए जमा करें", "te": "సమీక్ష కోసం సమర్పించండి", "ta": "மதிப்பாய்வுக்காக சமர்ப்பிக்கவும்", "pa": "ਸਮੀਖਿਆ ਲਈ ਜਮ੍ਹਾਂ ਕਰੋ", "ml": "അവലോകനത്തിനായി സമർപ്പിക്കുക"},
    "your_submissions_subheader": {"en": "Your submissions", "hi": "आपकी प्रस्तुतियाँ", "te": "మీ సమర్పణలు", "ta": "உங்கள் சமர்ப்பணங்கள்", "pa": "ਤੁਹਾਡੀਆਂ ਜਮ੍ਹਾਂਕਰਨਾਂ", "ml": "നിങ്ങളുടെ സമർപ്പണങ്ങൾ"},
    "no_submissions_msg": {"en": "You haven't submitted any documents yet.", "hi": "आपने अभी तक कोई दस्तावेज़ जमा नहीं किया है।", "te": "మీరు ఇంకా ఏ పత్రాలను సమర్పించలేదు.", "ta": "நீங்கள் இன்னும் எந்த ஆவணத்தையும் சமர்ப்பிக்கவில்லை.", "pa": "ਤੁਸੀਂ ਹਾਲੇ ਤੱਕ ਕੋਈ ਦਸਤਾਵੇਜ਼ ਜਮ੍ਹਾਂ ਨਹੀਂ ਕੀਤਾ।", "ml": "നിങ്ങൾ ഇതുവരെ രേഖകളൊന്നും സമർപ്പിച്ചിട്ടില്ല."},
    "header_citizen_submissions": {"en": "👤 Citizen Submissions", "hi": "👤 नागरिक प्रस्तुतियाँ", "te": "👤 పౌరుల సమర్పణలు", "ta": "👤 குடிமக்கள் சமர்ப்பணங்கள்", "pa": "👤 ਨਾਗਰਿਕ ਜਮ੍ਹਾਂਕਰਨ", "ml": "👤 പൗര സമർപ്പണങ്ങൾ"},
    "header_ingest": {"en": "📥 Ingest New Document", "hi": "📥 नया दस्तावेज़ जोड़ें", "te": "📥 కొత్త పత్రాన్ని జోడించండి", "ta": "📥 புதிய ஆவணத்தைச் சேர்க்கவும்", "pa": "📥 ਨਵਾਂ ਦਸਤਾਵੇਜ਼ ਸ਼ਾਮਲ ਕਰੋ", "ml": "📥 പുതിയ രേഖ ചേർക്കുക"},
    "header_review_queue": {"en": "🔍 Pending Review Queue", "hi": "🔍 लंबित समीक्षा कतार", "te": "🔍 పెండింగ్ సమీక్ష క్యూ", "ta": "🔍 நிலுவையிலுள்ள மதிப்பாய்வு வரிசை", "pa": "🔍 ਬਕਾਇਆ ਸਮੀਖਿਆ ਕਤਾਰ", "ml": "🔍 തീർപ്പാകാത്ത അവലോകന ക്യൂ"},
    "header_full_registry": {"en": "📊 Full Registry & Ledger", "hi": "📊 पूर्ण रजिस्ट्री और लेजर", "te": "📊 పూర్తి రిజిస్ట్రీ & లెడ్జర్", "ta": "📊 முழு பதிவேடு & லெட்ஜர்", "pa": "📊 ਪੂਰੀ ਰਜਿਸਟਰੀ ਅਤੇ ਲੈਜਰ", "ml": "📊 പൂർണ്ണ രജിസ്ട്രിയും ലെഡ്ജറും"},
    "header_manage_staff": {"en": "🧑‍💼 Manage Staff", "hi": "🧑‍💼 स्टाफ़ प्रबंधित करें", "te": "🧑‍💼 సిబ్బందిని నిర్వహించండి", "ta": "🧑‍💼 பணியாளர்களை நிர்வகிக்கவும்", "pa": "🧑‍💼 ਸਟਾਫ਼ ਦਾ ਪ੍ਰਬੰਧਨ ਕਰੋ", "ml": "🧑‍💼 സ്റ്റാഫിനെ നിയന്ത്രിക്കുക"},
    "header_staff_attendance": {"en": "🟢 Staff Attendance", "hi": "🟢 स्टाफ़ उपस्थिति", "te": "🟢 సిబ్బంది హాజరు", "ta": "🟢 பணியாளர் வருகை", "pa": "🟢 ਸਟਾਫ਼ ਹਾਜ਼ਰੀ", "ml": "🟢 സ്റ്റാഫ് ഹാജർ"},
    "header_staff_progress": {"en": "📈 Staff Progress", "hi": "📈 स्टाफ़ प्रगति", "te": "📈 సిబ్బంది పురోగతి", "ta": "📈 பணியாளர் முன்னேற்றம்", "pa": "📈 ਸਟਾਫ਼ ਤਰੱਕੀ", "ml": "📈 സ്റ്റാഫ് പുരോഗതി"},
}


def _default_lang():
    return st.session_state.get("lang", "en")


def t(key, **kwargs):
    """Looks up `key` in TRANSLATIONS for the current session language, falling
    back to English (and then the raw key) if a translation is missing."""
    entry = TRANSLATIONS.get(key, {})
    text = entry.get(_default_lang()) or entry.get("en") or key
    return text.format(**kwargs) if kwargs else text


def language_selector(container=st, key="lang_select_main"):
    if "lang" not in st.session_state:
        st.session_state.lang = "en"
    codes = list(LANGUAGE_NAMES.keys())
    chosen = container.selectbox(
        t("language_label"),
        codes,
        index=codes.index(st.session_state.lang),
        format_func=lambda c: LANGUAGE_NAMES[c],
        key=key,
    )
    if chosen != st.session_state.lang:
        st.session_state.lang = chosen
        st.rerun()


def password_strength_ok(password):
    """Mirrors the backend's policy: 8+ chars, an uppercase letter, a digit,
    and a special character. Returns True/False for use before submitting."""
    import re as _re
    return bool(
        password
        and len(password) >= 8
        and _re.search(r"[A-Z]", password)
        and _re.search(r"[0-9]", password)
        and _re.search(r"[^A-Za-z0-9]", password)
    )


def status_badge(label, kind):
    """kind: 'pending' | 'approved' | 'rejected' | 'online' | 'offline'"""
    return f'<span class="rr-badge rr-badge--{kind}">{label}</span>'

if "token" not in st.session_state:
    st.session_state.token = None
    st.session_state.user = None
if "login_stage" not in st.session_state:
    st.session_state.login_stage = "phone"  # phone -> signup | set_password | login
if "login_phone" not in st.session_state:
    st.session_state.login_phone = None
if "last_extraction" not in st.session_state:
    st.session_state.last_extraction = None


def auth_headers():
    return {"Authorization": f"Bearer {st.session_state.token}"}


def api_post(path, **kwargs):
    resp = requests.post(f"{API_BASE_URL}{path}", **kwargs)
    if resp.status_code >= 400:
        try:
            st.error(resp.json().get("detail", resp.text))
        except ValueError:
            st.error(resp.text)
        return None
    return resp.json()


def api_get(path, **kwargs):
    resp = requests.get(f"{API_BASE_URL}{path}", **kwargs)
    if resp.status_code >= 400:
        try:
            st.error(resp.json().get("detail", resp.text))
        except ValueError:
            st.error(resp.text)
        return None
    return resp.json()


def time_ago(iso_str):
    """Turns an ISO timestamp string from the API into 'Xm ago' style text."""
    if not iso_str:
        return "Never"
    try:
        dt = datetime.fromisoformat(iso_str)
    except (ValueError, TypeError):
        return str(iso_str)
    seconds = (datetime.utcnow() - dt).total_seconds()
    if seconds < 60:
        return "just now"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}m ago"
    hours = int(minutes // 60)
    if hours < 24:
        return f"{hours}h ago"
    return f"{int(hours // 24)}d ago"


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

def _reset_login_flow():
    st.session_state.login_stage = "phone"
    st.session_state.login_phone = None


def login_screen():
    st.markdown("<div style='text-align:center; margin-top:1.2rem; font-size:2.4rem;'>🗺️</div>", unsafe_allow_html=True)
    col_l, col_mid, col_r = st.columns([1, 1.3, 1])
    with col_mid:
        language_selector(st, key="lang_select_login")
        with st.container(border=True):
            st.markdown(f"<h2 style='text-align:center; margin-bottom:0;'>{t('app_name')}</h2>", unsafe_allow_html=True)
            st.markdown(
                f"<p style='text-align:center; color:var(--rr-muted); margin-top:2px;'>{t('login_subtitle')}</p>",
                unsafe_allow_html=True,
            )

            stage = st.session_state.login_stage

            # ---- Stage 1: phone number only -----------------------------
            if stage == "phone":
                with st.form("phone_form"):
                    phone = st.text_input(t("phone_label"), placeholder=t("phone_placeholder"))
                    if st.form_submit_button(t("continue_btn"), use_container_width=True) and phone.strip():
                        status = api_get("/api/auth/account-status", params={"phone": phone.strip()})
                        if status:
                            st.session_state.login_phone = phone.strip()
                            if not status["exists"]:
                                st.session_state.login_stage = "signup"
                            elif not status["has_password"]:
                                st.session_state.login_stage = "set_password"
                            else:
                                st.session_state.login_stage = "login"
                            st.rerun()

            # ---- Stage 2a: brand-new number -> full signup ---------------
            elif stage == "signup":
                st.info(t("not_registered_notice", phone=st.session_state.login_phone))
                with st.form("signup_form"):
                    name = st.text_input(t("full_name_label"))
                    st.caption(t("name_help"))
                    password = st.text_input(t("choose_password_label"), type="password")
                    st.markdown(f"<div class='rr-pw-hint'>{t('password_requirements')}</div>", unsafe_allow_html=True)
                    confirm = st.text_input(t("confirm_password_label"), type="password")
                    col1, col2 = st.columns(2)
                    submit = col1.form_submit_button(t("create_account_btn"), use_container_width=True)
                    back = col2.form_submit_button(t("use_different_number_btn"), use_container_width=True)
                    if submit:
                        if not name.strip():
                            st.warning("Name is required.")
                        elif not password_strength_ok(password):
                            st.warning(t("password_requirements"))
                        elif password != confirm:
                            st.warning("Passwords don't match.")
                        else:
                            result = api_post("/api/auth/signup", json={
                                "phone": st.session_state.login_phone, "name": name.strip(), "password": password,
                            })
                            if result:
                                st.session_state.token = result["token"]
                                st.session_state.user = result
                                _reset_login_flow()
                                st.rerun()
                    if back:
                        _reset_login_flow()
                        st.rerun()

            # ---- Stage 2b: existing account, no password yet -------------
            elif stage == "set_password":
                st.info(t("set_password_notice", phone=st.session_state.login_phone))
                with st.form("set_password_form"):
                    password = st.text_input(t("choose_password_label"), type="password")
                    st.markdown(f"<div class='rr-pw-hint'>{t('password_requirements')}</div>", unsafe_allow_html=True)
                    confirm = st.text_input(t("confirm_password_label"), type="password")
                    col1, col2 = st.columns(2)
                    submit = col1.form_submit_button(t("set_password_btn"), use_container_width=True)
                    back = col2.form_submit_button(t("use_different_number_btn"), use_container_width=True)
                    if submit:
                        if not password_strength_ok(password):
                            st.warning(t("password_requirements"))
                        elif password != confirm:
                            st.warning("Passwords don't match.")
                        else:
                            result = api_post("/api/auth/set-initial-password", json={
                                "phone": st.session_state.login_phone, "password": password,
                            })
                            if result:
                                st.session_state.token = result["token"]
                                st.session_state.user = result
                                _reset_login_flow()
                                st.rerun()
                    if back:
                        _reset_login_flow()
                        st.rerun()

            # ---- Stage 2c: existing account with a password -> login -----
            elif stage == "login":
                st.caption(t("login_as_caption", phone=st.session_state.login_phone))
                with st.form("login_form"):
                    password = st.text_input(t("password_label"), type="password")
                    col1, col2 = st.columns(2)
                    submit = col1.form_submit_button(t("login_btn"), use_container_width=True)
                    back = col2.form_submit_button(t("use_different_number_btn"), use_container_width=True)
                    if submit and password:
                        result = api_post("/api/auth/login", json={
                            "phone": st.session_state.login_phone, "password": password,
                        })
                        if result:
                            st.session_state.token = result["token"]
                            st.session_state.user = result
                            _reset_login_flow()
                            st.rerun()
                    if back:
                        _reset_login_flow()
                        st.rerun()


if not st.session_state.token:
    login_screen()
    st.stop()

user = st.session_state.user

st.sidebar.markdown(
    f"""
    <div class="rr-brand">
        <span class="rr-brand-mark">🗺️</span>
        <span class="rr-brand-word">{t("app_name")}</span>
    </div>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Account menu -- a compact avatar button that opens a popover with identity
# details, the language switcher, and logout. Keeping these behind one click
# (instead of a permanently-open identity card) leaves the sidebar's space
# for navigation.
# ---------------------------------------------------------------------------
role_tags = []
if user.get("is_staff"):
    role_tags.append(user.get("role") or "Registry Staff")
if user.get("is_admin"):
    role_tags.append("Admin")
tags_html = "".join(f'<span class="rr-tag">{rt}</span>' for rt in role_tags)

initials = "".join(part[0] for part in user["name"].split()[:2]).upper() or "?"

acct_col, _spacer_col = st.sidebar.columns([1, 2.4])
with acct_col:
    with st.popover(f"👤 {initials}", use_container_width=True):
        st.markdown(
            f"""
            <div class="rr-identity-card" style="margin-bottom:12px;">
                <div class="rr-identity-name">{user['name']}</div>
                <div class="rr-identity-phone">{user['phone']}</div>
                <div>{tags_html}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        language_selector(st, key="lang_select_sidebar")
        st.divider()
        if st.button(t("logout_btn"), use_container_width=True, key="logout_btn_popover"):
            api_post("/api/auth/logout", json={}, headers=auth_headers())
            st.session_state.token = None
            st.session_state.user = None
            st.rerun()

st.sidebar.divider()

# ---------------------------------------------------------------------------
# Navigation -- a vertical list of section buttons down the left side,
# replacing the old horizontal tab strip (which overflowed and wrapped
# awkwardly once labels got longer in some languages). The active section
# is highlighted by giving its button the "primary" (filled) style while
# every other button stays "secondary" (outline).
# ---------------------------------------------------------------------------
if user.get("is_staff"):
    nav_items = [
        ("my_records", t("tab_my_records")),
        ("citizen_submissions", t("tab_citizen_submissions")),
        ("ingest", t("tab_ingest")),
        ("review_queue", t("tab_review_queue")),
        ("full_registry", t("tab_full_registry")),
    ]
    if user.get("is_admin"):
        nav_items.extend([
            ("staff_attendance", t("tab_staff_attendance")),
            ("staff_progress", t("tab_staff_progress")),
            ("manage_staff", t("tab_manage_staff")),
        ])
else:
    nav_items = [
        ("my_records", t("tab_my_records")),
        ("submit_document", t("tab_submit_document")),
    ]

valid_section_keys = [key for key, _ in nav_items]
if st.session_state.get("active_section") not in valid_section_keys:
    st.session_state.active_section = valid_section_keys[0]

for section_key, section_label in nav_items:
    if st.sidebar.button(
        section_label,
        key=f"nav_{section_key}",
        use_container_width=True,
        type="primary" if st.session_state.active_section == section_key else "secondary",
    ):
        st.session_state.active_section = section_key
        st.rerun()

st.sidebar.divider()

# ---------------------------------------------------------------------------
# Live updates -- always on, fixed at a 30s interval. No toggle or slider is
# shown; the app simply reruns itself every 30 seconds in the background so
# new submissions, assignments, staff online status, etc. show up without
# the user ever refreshing the browser (which would clear st.session_state
# and force a re-login).
# ---------------------------------------------------------------------------
REFRESH_INTERVAL_SECONDS = 30
if st_autorefresh is not None:
    st_autorefresh(interval=REFRESH_INTERVAL_SECONDS * 1000, key="app_autorefresh")
    st.sidebar.caption(f"🔄 Auto-refreshing every {REFRESH_INTERVAL_SECONDS}s")


# ---------------------------------------------------------------------------
# Citizen view
# ---------------------------------------------------------------------------

def render_my_records():
    st.header(t("header_my_records"))
    data = api_get("/api/my-records", headers=auth_headers())
    if not data:
        return
    parcels = data["parcels"]
    if not parcels:
        st.info(t("no_parcels_msg"))
        return
    for p in parcels:
        with st.container(border=True):
            st.subheader(f"Survey No. {p['survey_no']}")
            st.write(f"**Owner:** {p['owner_name']} | **Area:** {p['area_hectares']} hectares")
            if p.get("parent_plot"):
                st.caption(f"Parent plot: {p['parent_plot']}")
            if p.get("document_id"):
                doc_resp = requests.get(f"{API_BASE_URL}/api/documents/{p['document_id']}", headers=auth_headers())
                if doc_resp.status_code == 200:
                    st.download_button(
                        t("download_doc_btn"),
                        data=doc_resp.content,
                        file_name=f"land_deed_{p['survey_no']}.jpg",
                        key=f"dl_{p['id']}",
                    )


def render_submit_document():
    st.header(t("header_submit_document"))
    st.caption(t("submit_document_caption"))

    uploaded = st.file_uploader(t("upload_document_label"), type=["png", "jpg", "jpeg"], key="citizen_upload")
    claimed_survey_no = st.text_input(t("survey_no_label"))
    note = st.text_area(t("staff_note_label"))
    if uploaded and st.button(t("submit_review_btn")):
        files = {"file": (uploaded.name, uploaded.getvalue(), uploaded.type)}
        data = {"claimed_survey_no": claimed_survey_no, "note": note}
        with st.spinner("Uploading and analyzing your document... this can take 10-30 seconds (longer if the server just woke up from idle)."):
            result = api_post("/api/citizen/submit-document", files=files, data=data, headers=auth_headers())
        if result:
            st.success("Submitted. You'll see it below once staff review it — this can take some time.")
            st.rerun()

    st.divider()
    st.subheader(t("your_submissions_subheader"))
    data = api_get("/api/citizen/my-submissions", headers=auth_headers())
    if not data or not data["submissions"]:
        st.info(t("no_submissions_msg"))
        return
    badge_kind = {"PENDING": "pending", "APPROVED": "approved", "REJECTED": "rejected"}
    for s in data["submissions"]:
        with st.container(border=True):
            st.markdown(
                f"{status_badge(s['status'].title(), badge_kind.get(s['status'], 'pending'))}"
                f"&nbsp;&nbsp;<span class='rr-mono' style='color:var(--rr-muted); font-size:0.85rem;'>submitted {s['created_at']}</span>",
                unsafe_allow_html=True,
            )
            if s.get("claimed_survey_no"):
                st.caption(f"Claimed survey no: {s['claimed_survey_no']}")
            if s["status"] == "REJECTED" and s.get("rejection_reason"):
                st.error(f"Reason: {s['rejection_reason']}")


# ---------------------------------------------------------------------------
# Staff views
# ---------------------------------------------------------------------------

def render_citizen_submissions():
    st.header(t("header_citizen_submissions"))

    scope = "mine"
    if user.get("is_admin"):
        view_choice = st.radio(
            "View",
            ["My queue", "All submissions (reassign)"],
            horizontal=True,
            key="submissions_view_choice",
        )
        scope = "mine" if view_choice == "My queue" else "all"
        if scope == "all" and st.button("↻ Auto-assign any unassigned submissions"):
            result = api_post("/api/admin/rebalance-unassigned", json={}, headers=auth_headers())
            if result:
                st.success(f"Assigned {result['reassigned_count']} previously unassigned submission(s).")
                st.rerun()

    st.caption(
        "Documents citizens uploaded themselves, waiting on review and approval."
        if scope == "mine"
        else "Every pending submission across all staff — reassign as needed."
    )
    data = api_get(f"/api/staff/submissions?scope={scope}", headers=auth_headers())
    if not data:
        return
    if not data["submissions"]:
        st.info("No citizen submissions pending review." if scope == "mine" else "No pending submissions at all.")
        return

    active_staff = None
    if scope == "all":
        roster = api_get("/api/staff/list-active", headers=auth_headers())
        active_staff = roster["staff"] if roster else []

    for s in data["submissions"]:
        with st.container(border=True):
            st.subheader(f"Submitted by {s['submitter_name']} ({s['submitter_phone']})")
            st.caption(f"Uploaded {s['created_at']} — file: {s['file_name']}")
            if scope == "all":
                assigned_label = s.get("assigned_to_name") or s.get("assigned_to") or "Unassigned"
                st.caption(f"Assigned to: **{assigned_label}**")
                staff_names = [f"{st_['name']} ({st_['phone']})" for st_ in active_staff]
                staff_phones = [st_["phone"] for st_ in active_staff]
                current_idx = staff_phones.index(s["assigned_to"]) if s.get("assigned_to") in staff_phones else 0
                col_a, col_b = st.columns([3, 1])
                new_choice = col_a.selectbox(
                    "Reassign to", staff_names, index=current_idx, key=f"reassign_select_{s['submission_id']}", label_visibility="collapsed"
                )
                if col_b.button("Reassign", key=f"reassign_btn_{s['submission_id']}"):
                    new_phone = staff_phones[staff_names.index(new_choice)]
                    result = api_post(
                        f"/api/staff/submissions/{s['submission_id']}/reassign",
                        json={"assigned_to": new_phone},
                        headers=auth_headers(),
                    )
                    if result:
                        st.success("Reassigned.")
                        st.rerun()
            if s.get("claimed_survey_no"):
                st.write(f"Claimed survey no: **{s['claimed_survey_no']}**")
            if s.get("note"):
                st.write(f"Note from citizen: {s['note']}")

            flags = s.get("authenticity_flags") or []
            if flags:
                st.error("🚩 Authenticity signals — review closely, not proof of forgery:")
                for f in flags:
                    st.write(f"- {f}")

            doc_resp = requests.get(f"{API_BASE_URL}/api/documents/{s['document_id']}", headers=auth_headers())
            if doc_resp.status_code == 200:
                st.image(doc_resp.content, width=400)

            with st.form(f"approve_form_{s['submission_id']}"):
                col1, col2 = st.columns(2)
                survey_no = col1.text_input("Survey Number", value=s.get("claimed_survey_no") or "", key=f"sn_{s['submission_id']}")
                owner_name = col2.text_input("Owner Name", value=s["submitter_name"], key=f"on_{s['submission_id']}")
                col3, col4 = st.columns(2)
                area = col3.number_input("Area (hectares)", value=0.0, format="%.4f", key=f"area_{s['submission_id']}")
                parent_plot = col4.text_input("Parent Plot (optional)", key=f"pp_{s['submission_id']}")
                approve = st.form_submit_button("✅ Approve & Register")

                if approve:
                    payload = {
                        "survey_no": survey_no,
                        "owner_name": owner_name,
                        "owner_phone": s["submitter_phone"],
                        "area_hectares": area,
                        "parent_plot": parent_plot or None,
                    }
                    commit_result = api_post(f"/api/staff/submissions/{s['submission_id']}/approve", json=payload, headers=auth_headers())
                    if commit_result:
                        st.success(f"Approved and registered. Block hash: {commit_result['block_hash']}")
                        st.rerun()

            with st.popover("❌ Reject this submission"):
                reason = st.text_area("Reason for rejection", key=f"reason_{s['submission_id']}")
                if st.button("Confirm Rejection", key=f"reject_btn_{s['submission_id']}"):
                    if reason.strip():
                        result = api_post(f"/api/staff/submissions/{s['submission_id']}/reject", json={"reason": reason}, headers=auth_headers())
                        if result:
                            st.rerun()
                    else:
                        st.warning("Please give a reason so the citizen understands why.")


def render_manage_staff():
    st.header(t("header_manage_staff"))
    st.caption("Add colleagues who need staff access (reviewing submissions, approving records).")

    with st.form("add_staff_form"):
        col1, col2 = st.columns(2)
        phone = col1.text_input("Phone Number")
        name = col2.text_input("Full Name")
        role = st.text_input("Role / Title (optional)", placeholder="e.g. Approval Manager, Registry Clerk")
        if st.form_submit_button("Add Staff Member"):
            if phone.strip() and name.strip():
                result = api_post("/api/staff/add", json={"phone": phone.strip(), "name": name.strip(), "role": role.strip() or None}, headers=auth_headers())
                if result:
                    st.success(f"Added {result['staff']['name']} ({result['staff']['phone']}) as staff.")
                    st.rerun()
            else:
                st.warning("Phone number and name are both required.")

    st.divider()
    st.subheader("Current staff")
    st.caption("Toggle Admin or Staff Access, or edit someone's role, then click Save Changes below. Unchecking 'Staff Access' removes their access entirely.")
    data = api_get("/api/staff/list", headers=auth_headers())
    if data and data["staff"]:
        original = data["staff"]
        editable_rows = [
            {
                "phone": s["phone"],
                "name": s["name"],
                "role": s.get("role") or "",
                "is_admin": s["is_admin"],
                "is_staff": s["is_staff"],
            }
            for s in original
        ]
        edited_rows = st.data_editor(
            editable_rows,
            use_container_width=True,
            hide_index=True,
            disabled=["phone", "name"],
            column_config={
                "phone": st.column_config.TextColumn("Phone"),
                "name": st.column_config.TextColumn("Name"),
                "role": st.column_config.TextColumn("Role / Title"),
                "is_admin": st.column_config.CheckboxColumn("Admin"),
                "is_staff": st.column_config.CheckboxColumn("Staff Access"),
            },
            key="staff_editor",
        )

        if st.button("Save Changes"):
            original_by_phone = {s["phone"]: s for s in original}
            changed_any = False
            for row in edited_rows:
                before = original_by_phone.get(row["phone"])
                if not before:
                    continue
                if (before["role"] or "") != row["role"] or before["is_admin"] != row["is_admin"] or before["is_staff"] != row["is_staff"]:
                    changed_any = True
                    result = api_post(
                        "/api/staff/update",
                        json={
                            "phone": row["phone"],
                            "is_staff": row["is_staff"],
                            "is_admin": row["is_admin"],
                            "role": row["role"],
                        },
                        headers=auth_headers(),
                    )
                    if result:
                        st.success(f"Updated {row['name']} ({row['phone']}).")
            if not changed_any:
                st.info("No changes to save.")
            else:
                st.rerun()


def render_staff_attendance():
    import datetime as _dt

    st.header(t("header_staff_attendance"))
    st.caption(
        "Live online/offline status, plus login and logout history. 'Online' reflects "
        "recent activity in the app — a staff member idle for a while with the tab still "
        "open will eventually show offline again."
    )

    data_now = api_get("/api/admin/staff-attendance", headers=auth_headers())
    if not data_now:
        return

    st.subheader("Live status")
    for r in data_now["staff"]:
        kind = "online" if r["effective_online"] else "offline"
        status_text = "Online now" if r["effective_online"] else f"Last seen {time_ago(r['last_seen_at'])}"
        col1, col2 = st.columns([3, 2])
        col1.markdown(
            f"<span class='rr-dot rr-dot--{kind}'></span>"
            f"<strong>{r['name']}</strong> <span class='rr-mono' style='color:var(--rr-muted); font-size:0.85rem;'>({r['phone']})</span>"
            f" — {r.get('role') or 'Staff'}",
            unsafe_allow_html=True,
        )
        col2.markdown(status_badge(status_text, kind), unsafe_allow_html=True)

    st.divider()
    st.subheader("Login / logout history")
    selected_date = st.date_input("Date", value=_dt.date.today(), max_value=_dt.date.today(), key="attendance_date")
    data = api_get(f"/api/admin/staff-attendance?date={selected_date.isoformat()}", headers=auth_headers())
    if not data:
        return

    history_rows = []
    for r in data["staff"]:
        if not r["sessions"]:
            history_rows.append({"Name": r["name"], "Phone": r["phone"], "Login": "—", "Logout": "—"})
        else:
            for sess in r["sessions"]:
                history_rows.append(
                    {
                        "Name": r["name"],
                        "Phone": r["phone"],
                        "Login": sess["login_at"],
                        "Logout": sess["logout_at"] or "Not logged out yet",
                    }
                )
    st.dataframe(history_rows, use_container_width=True, hide_index=True)


def render_staff_progress():
    import datetime as _dt

    st.header(t("header_staff_progress"))
    st.caption("How many submissions each staff member has approved or rejected, day by day.")

    selected_date = st.date_input("Date", value=_dt.date.today(), max_value=_dt.date.today())
    data = api_get(f"/api/admin/staff-progress?date={selected_date.isoformat()}", headers=auth_headers())
    if not data:
        return
    rows = data["staff"]
    if not rows:
        st.info("No staff on record yet.")
        return

    display_rows = [
        {
            "Name": r["name"],
            "Phone": r["phone"],
            "Role": r.get("role") or "",
            "Approved": r["approved_today"],
            "Rejected": r["rejected_today"],
            "Currently pending": r["pending_now"],
        }
        for r in rows
    ]
    st.dataframe(display_rows, use_container_width=True, hide_index=True)

    totals_col1, totals_col2, totals_col3 = st.columns(3)
    totals_col1.metric("Total approved", sum(r["approved_today"] for r in rows))
    totals_col2.metric("Total rejected", sum(r["rejected_today"] for r in rows))
    totals_col3.metric("Total still pending", sum(r["pending_now"] for r in rows))

    chart_data = {r["name"]: r["approved_today"] + r["rejected_today"] for r in rows}
    if any(chart_data.values()):
        st.caption("Documents actioned per staff member on this date")
        st.bar_chart(chart_data)


def render_ingestion():
    st.header(t("header_ingest"))
    uploaded = st.file_uploader("Upload scanned land document", type=["png", "jpg", "jpeg"])
    if uploaded and st.button("Run Extraction"):
        files = {"file": (uploaded.name, uploaded.getvalue(), uploaded.type)}
        with st.spinner("Analyzing document... this can take 10-30 seconds (longer if the server just woke up from idle)."):
            result = api_post("/api/extract-and-validate", files=files, headers=auth_headers())
        if result:
            st.session_state.last_extraction = result

    result = st.session_state.last_extraction
    if result:
        extracted = result["extracted"]
        confidence = extracted["confidence"]

        flags = result.get("authenticity_flags", [])
        tamper_risk = extracted.get("tamper_risk", "low")
        if flags or tamper_risk in ("medium", "high"):
            st.error("🚩 **Authenticity signals detected — not proof of forgery, but review closely before approving.**")
            for f in flags:
                st.write(f"- {f}")
            if tamper_risk in ("medium", "high") and not extracted.get("tamper_signs"):
                st.write(f"- Visual tamper risk assessed as **{tamper_risk}** by the extraction model.")
        else:
            st.caption("✅ No fraud-risk signals detected (duplicate submission, edited-image metadata, or visible tampering). This does not confirm the document is genuine — only the issuing state's own verification portal can do that.")

        if result["needs_review"]:
            st.warning(f"⚠️ Flagged for review ({confidence * 100:.1f}% OCR confidence). Check every field carefully.")
        else:
            st.success(f"Extraction confidence: {confidence * 100:.1f}%. Please still verify — OCR informs, it never decides ownership.")
        for issue in result.get("validation_issues", []):
            (st.error if "DISPUTE" in issue else st.info)(issue)

        with st.form("commit_form"):
            survey_no = st.text_input("Survey / Khasra / Gat Number", value=extracted["survey_no"])
            owner_name = st.text_input("Owner Name", value=extracted["owner_name"])
            owner_phone = st.text_input("Owner Phone Number (required — this is how the owner logs in)")
            area = st.number_input("Area (hectares)", value=float(extracted["area_hectares"] or 0.0), format="%.4f")
            parent_plot = st.text_input("Parent Plot (optional)")
            if st.form_submit_button("Approve & Commit to Ledger"):
                if not owner_phone.strip():
                    st.error("Owner phone number is required so the owner can log in and see this record.")
                else:
                    payload = {
                        "document_id": result["document_id"],
                        "survey_no": survey_no,
                        "owner_name": owner_name,
                        "owner_phone": owner_phone.strip(),
                        "area_hectares": area,
                        "parent_plot": parent_plot or None,
                    }
                    commit_result = api_post("/api/commit-record", json=payload, headers=auth_headers())
                    if commit_result:
                        st.success(f"Committed. Block hash: {commit_result['block_hash']}")
                        st.session_state.last_extraction = None
                        st.rerun()


def render_review_queue():
    st.header(t("header_review_queue"))
    data = api_get("/api/review-queue", headers=auth_headers())
    if not data:
        return
    if not data["queue"]:
        st.info("No documents pending review.")
        return
    st.dataframe(data["queue"], use_container_width=True)
    st.caption("Re-run these through 'Ingest New Document' with the same file to complete registration once the owner's details are confirmed.")


def render_registry():
    st.header(t("header_full_registry"))
    data = api_get("/api/records", headers=auth_headers())
    if not data:
        return
    tab1, tab2 = st.tabs(["Cadastral Parcels", "Audit Ledger"])
    with tab1:
        st.dataframe(data["parcels"], use_container_width=True)
    with tab2:
        st.dataframe(data["ledger"], use_container_width=True)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Routing -- driven by st.session_state.active_section, set by the vertical
# nav buttons in the sidebar (see above).
# ---------------------------------------------------------------------------

SECTION_RENDERERS = {
    "my_records": render_my_records,
    "submit_document": render_submit_document,
    "citizen_submissions": render_citizen_submissions,
    "ingest": render_ingestion,
    "review_queue": render_review_queue,
    "full_registry": render_registry,
    "staff_attendance": render_staff_attendance,
    "staff_progress": render_staff_progress,
    "manage_staff": render_manage_staff,
}

SECTION_RENDERERS[st.session_state.active_section]()