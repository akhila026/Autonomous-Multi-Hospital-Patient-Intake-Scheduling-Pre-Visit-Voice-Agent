import re
from typing import List


class VoiceSynthesizer:
    """
    Speech Generation & Synthesis Helper (PRD Section 11).
    Transforms rich markdown agent responses into natural, acoustic conversational speech.
    Strips raw formatting, resolves abbreviations, and segments utterances for low latency.
    """

    @staticmethod
    def clean_text_for_speech(text: str) -> str:
        if not text:
            return ""

        # 1. Remove markdown links [text](url) -> text
        cleaned = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)

        # 2. Remove code fences and inline backticks
        cleaned = re.sub(r'```[a-zA-Z]*\n[\s\S]*?\n```', '', cleaned)
        cleaned = re.sub(r'`([^`]+)`', r'\1', cleaned)

        # 3. Remove bold/italic markdown (*, _, **)
        cleaned = re.sub(r'[\*_]{1,3}([^\*_]+)[\*_]{1,3}', r'\1', cleaned)

        # 4. Remove headers, bullet points, blockquotes
        cleaned = re.sub(r'^[#>\-\*•]\s*', '', cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r'•\s*', '', cleaned)

        # 5. Remove emojis and visual icons
        cleaned = re.sub(r'[✅❌⚠️📋•\U00010000-\U0010ffff]', '', cleaned)

        # 6. Expand clinical and administrative abbreviations for clear acoustic prosody
        cleaned = re.sub(r'\b[Dd]r\.(?=\s|$)', 'Doctor', cleaned)
        cleaned = re.sub(r'\b[Dd]r\b', 'Doctor', cleaned)
        cleaned = re.sub(r'\bEHR\b', 'E.H.R.', cleaned)
        cleaned = re.sub(r'\bID\b', 'I.D.', cleaned)
        cleaned = re.sub(r'\bMRN\b', 'M.R.N.', cleaned)
        cleaned = re.sub(r'\bAM\b', 'A.M.', cleaned)
        cleaned = re.sub(r'\bPM\b', 'P.M.', cleaned)
        cleaned = re.sub(r'\$(\d+)', r'\1 dollars', cleaned)

        # 7. Normalize whitespace
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()

        return cleaned

    @staticmethod
    def segment_sentences(text: str) -> List[str]:
        """Segments cleaned speech text into natural spoken utterances for streaming playback."""
        cleaned = VoiceSynthesizer.clean_text_for_speech(text)
        if not cleaned:
            return []

        # Split on sentence terminals
        raw_sentences = re.split(r'(?<=[.!?])\s+', cleaned)
        sentences = [s.strip() for s in raw_sentences if s.strip()]
        return sentences if sentences else [cleaned]
