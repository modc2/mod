import os
import mod as m
from typing import List, Dict, Union, Tuple, Any, Optional
import torch
import json
from torch import nn
from transformers import AutoModelForCausalLM, AutoTokenizer

"""
Transformer Model Implementation
A clean, efficient implementation of a transformer-based language model with fine-tuning capabilities.
"""

_cache = {}  # {model_id: (model, tokenizer, device)}

class Transformer(nn.Module):

    def __init__(self,
                model: str = "1bitLLM/bitnet_b1_58-3B",
                tokenizer: Union[str, 'tokenizer'] = None,
                device: str = 'cuda',
                path: str = '~/.mod/hf',
                **kwargs):
        nn.Module.__init__(self)
        self.path = m.abspath(path)
        self.set_model(model=model, tokenizer=tokenizer, device=device)

    def set_model(self,
                  model: str = "1bitLLM/bitnet_b1_58-3B",
                  tokenizer: Union[str, 'tokenizer'] = None,
                  device: str = 'cuda',
                  **kwargs):
        if not torch.cuda.is_available():
            device = 'cpu'
        tok_id = tokenizer or model
        cache_key = f"{model}:{tok_id}:{device}"
        if cache_key in _cache:
            self.model, self.tokenizer, self.device = _cache[cache_key]
            return self.device
        self.model = AutoModelForCausalLM.from_pretrained(model)
        self.tokenizer = AutoTokenizer.from_pretrained(tok_id)
        self.model.to(device)
        self.device = device
        _cache[cache_key] = (self.model, self.tokenizer, self.device)
        return self.device
    def forward(self, x: str = None, output_hidden_states=True, **kwargs):
        tokenizer_output = self.tokenizer(x, **kwargs)
        input_ids = tokenizer_output.input_ids.to(self.device)
        return  self.model(input_ids=input_ids, output_hidden_states=output_hidden_states)

    def generate(self, text: str = "Today is a beautiful day, and", max_length: int = 20):
        """Generate text from a given prompt."""
        from transformers import (
            LogitsProcessorList,
            MinLengthLogitsProcessor,
            TopKLogitsWarper,
            TemperatureLogitsWarper,
            StoppingCriteriaList,
            MaxLengthCriteria,
        )

        self.model.config.pad_token_id = self.model.config.eos_token_id
        input_ids = self.tokenizer(text, return_tensors="pt").input_ids.to(self.device)

        logits_processor = LogitsProcessorList([
            MinLengthLogitsProcessor(15, eos_token_id=self.model.config.eos_token_id),
        ])
        
        logits_warper = LogitsProcessorList([
            TopKLogitsWarper(50),
            TemperatureLogitsWarper(0.7),
        ])

        stopping_criteria = StoppingCriteriaList([MaxLengthCriteria(max_length=max_length)])

        torch.manual_seed(0)
        with torch.no_grad():
            outputs = self.model.sample(
                input_ids,
                logits_processor=logits_processor,
                logits_warper=logits_warper,
                stopping_criteria=stopping_criteria,
            )
            
        output_text = self.tokenizer.batch_decode(outputs, skip_special_tokens=True)
        return output_text