"""Read progress emitted by our owned local model; never guess a completion ETA."""
import re


def model_progress(line):
    match=re.search(r'Generation queued:.*prompt_tokens=(\d+)',line)
    if match:return {'stage':'Model reading context','prompt_tokens':int(match[1]),'processed_tokens':0}
    match=re.search(r'Prefill progress:.*tokens=(\d+)/(\d+)',line)
    if match:return {'stage':'Model reading context','processed_tokens':int(match[1]),'prompt_tokens':int(match[2])}
    match=re.search(r'Decode progress:.*generated_tokens=(\d+).*rate=(\d+(?:\.\d+)?)',line)
    if match:return {'stage':'Model writing response','generated_tokens':int(match[1]),'tokens_per_second':float(match[2])}
    return None
