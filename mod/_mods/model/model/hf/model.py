import os
import mod as c
from typing import List, Dict, Union, Tuple, Any, Optional
import torch
import json
from torch import nn
from transformers import AutoModelForCausalLM, AutoTokenizer

"""
Transformer Model Implementation
A clean, efficient implementation of a transformer-based language model with fine-tuning capabilities.
"""

class Transformer(nn.Module):
    models = [
        "1bitLLM/bitnet_b1_58-3B"
    ]

    def __init__(self,
                model: str = "1bitLLM/bitnet_b1_58-3B",
                tokenizer: Union[str, 'tokenizer'] = None,
                optimizer: torch.optim = 'torch.optim.Adam',
                device: str = 'cuda',
                num_layers: int = 1,
                lr: float = 0.00002,
                topk: int = 4096,
                state_path: str = None,
                **kwargs):
        nn.Module.__init__(self)
        self.state_path = self.resolve_path(state_path or model)
        self.model = AutoModelForCausalLM.from_pretrained(model)
        self.tokenizer = AutoTokenizer.from_pretrained(tokenizer or model)
        self.optimizer = c.obj(optimizer)(self.model.parameters(), lr=lr)
        self.resolve_device(device)
        self.set_fine_tuning_params(num_layers=num_layers)

    def forward(self, x: str = None, **kwargs):
        model_output = self.model(input_ids=self.process(x),
                                  output_hidden_states=True)
        return model_output

    def resolve_device(self, device: str = 'cuda') -> torch.device:
        if not torch.cuda.is_available():
            device = 'cpu'
        self.model.to(device)
        self.device = device
        return self.device

    def loss_fct(self, logits: torch.FloatTensor, labels: torch.LongTensor) -> torch.FloatTensor:
        """
        Calculate CausalLM loss for next-token prediction.
        
        Args:
            logits: [batch_size, sequence_len, vocab_size]
            labels: [batch_size, sequence_len]
        
        Returns:
            loss: scalar tensor
        """
        if not hasattr(self, '_loss_fn'):
            self._loss_fn = torch.nn.CrossEntropyLoss()
        shift_logits = logits[..., :-1, :].contiguous()
        shift_labels = labels[..., 1:].contiguous()
        loss = self._loss_fn(shift_logits.view(-1, shift_logits.size(-1)), shift_labels.view(-1))
        return loss

    def process(self, x: str = 'Whadup', **kwargs) -> torch.Tensor:
        """Returns tokenized text as torch tensor."""
        tokenizer_output = self.tokenizer(x, **kwargs)
        return tokenizer_output.input_ids.to(self.device)

    def save(self, path: str = None, trainable_only: bool = True):
        path = path or self.state_path
        model_state_dict = self.model.state_dict()
        
        if trainable_only:
            model_state_dict = {k: v for k, v in model_state_dict.items() if v.requires_grad}
    
        os.makedirs(os.path.dirname(path), exist_ok=True)
        state_dict = {
            'model': model_state_dict,
            'optimizer': self.optimizer.state_dict(),
        }
        torch.save(state_dict, path)
        return path

    def load(self, path: str = None):
        path = path or self.state_path
        loaded_state = torch.load(path)
        state_dict = self.model.state_dict()
        
        for k, v in loaded_state['model'].items():
            assert k in state_dict
            state_dict[k] = v
            
        self.model.load_state_dict(state_dict)
        self.optimizer.load_state_dict(loaded_state['optimizer'])

    def set_fine_tuning_params(self, num_layers: int = 1, layer_name: str = None, all: bool = False) -> Tuple[bool, str]:
        """
        Set fine-tuning parameters for the model.
        
        Returns:
            reached_last_layer: If we have set partial model to requires_grad
            last_layer_name: The name of the last layer
        """
        def find_last_layer(model: torch.nn.Module) -> Optional[str]:
            """Recursively find the last layer in a nn.ModuleList"""
            reverted_child_list = [(name, child) for name, child in model.named_children()]
            reverted_child_list.reverse()

            for name, child in reverted_child_list:
                if isinstance(child, nn.ModuleList):
                    if num_layers > len(child):
                        c.print(f'Number of finetune layers was set higher than the layers available {len(child)}')
                        return None
                    return (name + '.' + str(len(child) - num_layers))
                
            for name, child in reverted_child_list:
                name_ = find_last_layer(child)
                if name_ is not None:
                    return (name + '.' + name_)

            return None

        if layer_name is None:
            last_layer_name = find_last_layer(self.model)
        else:
            last_layer_name = layer_name

        reached_last_layer = False

        if all or (last_layer_name is None):
            return False, last_layer_name

        c.print(f'Set to finetune layer {last_layer_name} and onwards')
        
        for name, param in self.model.named_parameters():
            if last_layer_name in name or reached_last_layer:
                param.requires_grad = True
                reached_last_layer = True
            else:
                param.requires_grad = False

        if not reached_last_layer:
            if all:
                c.print('Set to finetune the whole model, this will significantly increase memory usage.')
            else:
                c.print(f'Cannot identify the last layer with name {last_layer_name}, setting to finetune all parameters.')

        return reached_last_layer, last_layer_name

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
            
        c.print(f'outputs: {outputs.shape}', 'purple')
        output_text = self.tokenizer.batch_decode(outputs, skip_special_tokens=True)
        return output_text

    def loss(self, pred: torch.Tensor,
             gt: torch.Tensor = None,
             input: torch.Tensor = None,
             return_value: bool = False,
             *args, **kwargs) -> torch.Tensor:
        """Calculate the loss for the model."""
        if input is not None:
            gt = input[:, -(pred.shape[1] - 1):].flatten()
            pred = pred[:, :pred.shape[1] - 1]
            
        if len(pred.shape) == 3:
            pred = pred.reshape(-1, pred.shape[-1])
        
        assert gt.shape == pred.shape[:1], f'gt.shape: {gt.shape} pred.shape: {pred.shape}'

        loss_fn = torch.nn.CrossEntropyLoss(*args, **kwargs)
        loss = loss_fn(pred, gt.to(pred.device))
        
        if return_value:
            return loss.item()
        return loss


if __name__ == "__main__":
    Transformer.run()
