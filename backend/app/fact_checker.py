import os
import re
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv, find_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_community.utilities import GoogleSearchAPIWrapper
from langchain_core.prompts import PromptTemplate

load_dotenv(find_dotenv(usecwd=True) or Path(__file__).resolve().parents[2] / ".env")

_llm: Optional[ChatGoogleGenerativeAI] = None
_search: Optional[GoogleSearchAPIWrapper] = None

def _ensure_google_search() -> GoogleSearchAPIWrapper:
    global _search
    if _search is not None:
        return _search
    api_missing = [key for key in ('GOOGLE_API_KEY', 'GOOGLE_CSE_ID') if not os.getenv(key)]
    if api_missing:
        raise RuntimeError(
            f"Missing Google Custom Search credentials: {', '.join(api_missing)}. "
            "Set them in your environment or .env file."
        )
    _search = GoogleSearchAPIWrapper()
    return _search

def _ensure_llm() -> ChatGoogleGenerativeAI:
    global _llm
    if _llm is not None:
        return _llm
    api_key = os.getenv('GOOGLE_API_KEY')
    creds_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
    if not api_key and not creds_path:
        raise RuntimeError(
            "No Gemini credentials found. Provide GOOGLE_API_KEY or GOOGLE_APPLICATION_CREDENTIALS "
            "before starting the backend."
        )
    _llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        temperature=0.1,
        google_api_key=api_key or None,
    )
    return _llm

# III. The Reasoning Prompt Template
RAG_PROMPT_TEMPLATE = """
**FACT-CHECKER ASSIGNMENT: RAG Fake News Detector**

You are an objective, expert fact-checker. Your task is to analyze a user's query against
the real-time evidence retrieved from Google Search.

**1. QUERY/CLAIM TO VERIFY:**
{query}

**2. RETRIEVED EVIDENCE (Search Results):**
{search_results}

**INSTRUCTIONS FOR REASONING:**
A. **Classification:** Determine the veracity of the QUERY.
   - If the search results overwhelmingly confirm the claim, classify it as **REAL**.
   - If the search results **contradict** or **cannot find any corroborating information** for the claim, classify it as **FAKE**.
B. **Reasoning:** Your explanation must explicitly reference the information found in the **RETRIEVED EVIDENCE** section.
C. **Evidence Sourcing:** After writing your reasoning, create a list of the source **links** (URLs) from the **RETRIEVED EVIDENCE** that directly support your conclusion.

**FINAL OUTPUT FORMAT:**
Classification: [REAL or FAKE]
Reasoning: [Provide a concise, detailed, and evidence-based explanation for your classification.]
Evidence: [
  "https://www.source-link-1.com/article",
  "https://www.source-link-2.com/news"
]
"""
RAG_PROMPT = PromptTemplate.from_template(RAG_PROMPT_TEMPLATE)

def _extract_section(text: str, label: str) -> str:
    pattern = re.compile(rf"{label}\s*(.*?)(?=\n[A-Z][a-zA-Z]+:|$)", re.IGNORECASE | re.DOTALL)
    match = pattern.search(text)
    return match.group(1).strip() if match else ""

def _normalise_classification(value: str) -> str:
    lowered = (value or "").lower()
    if any(token in lowered for token in ["real", "true", "verified"]):
        return "real"
    if any(token in lowered for token in ["fake", "false", "hoax"]):
        return "fake"
    return "unknown"

def _parse_fact_check_output(raw: str):
    classification = _extract_section(raw, "Classification:")
    reasoning = _extract_section(raw, "Reasoning:")
    evidence_block = _extract_section(raw, "Evidence:")
    evidence = re.findall(r"https?://[^\s\"')]+", evidence_block or "")
    return _normalise_classification(classification), reasoning or raw.strip(), evidence

def run_fact_check(claim: str):
    if not claim.strip():
        return {"error": "Claim must not be empty."}
    print(f"1. Verifying Claim: '{claim}'")
    try:
        search = _ensure_google_search()
        print("2. Performing Google Search...")
        search_results = search.results(claim, num_results=10)
        print("3. Evidence retrieved.")
    except Exception as e:
        print(f"Error during Google Search: {e}")
        return {"error": str(e)}

    try:
        llm = _ensure_llm()
        final_prompt = RAG_PROMPT.format(query=claim, search_results=search_results)
        print("4. Sending evidence to Gemini for Reasoning...")
        response = llm.invoke(final_prompt)
        raw_text = getattr(response, "content", str(response)).strip()
        classification, reasoning, evidence = _parse_fact_check_output(raw_text)
        return {
            "classification": classification,
            "reasoning": reasoning,
            "evidence": evidence,
            "raw": raw_text,
        }
    except Exception as e:
        print(f"Error during Gemini Reasoning: {e}")
        return {"error": str(e)}