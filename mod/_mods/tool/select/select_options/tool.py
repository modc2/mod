
import mod as c
import json
import os
from typing import List, Dict, Union, Optional, Any

print = c.print
class SelectOptions:


    goal = """
            Evaluate each option based on its relevance to the query
            - Return at most N options with their scores
            - Score range: MIN_SCORE (lowest) to MAX_SCORE (highest)
            - Only include options with scores >= THRESHOLD
            - Be conservative with scoring to prioritize quality over quantity
            - Respond ONLY with the JSON format specified below
            - IT NEEDS TO BE JSON AND IT NEEDS TO BE BETWEEN THE ANCHORS PROVIDED
        """
        
    def __init__(self, model='model.openrouter'):
        """
        Initialize the Find module.
        
        Args:
            model: Pre-initialized model instance (optional)
            default_provider: Provider to use if no model is provided
            default_model: Default model to use for ranking
        """
        self.model = c.mod(model)()

    def forward(self,  
              query: str = 'i want a what is most similar to a tiger', 
              options = ['a cat', 'a dog', 'a bird'],
              n: int = 1,  
              trials: int = 4,
              min_score: int = 0,
              max_score: int = 10,
              threshold: int = 5,
              model: str =  'anthropic/claude-opus-4',
              context: Optional[str] = None,
              temperature: float = 0.5,
              include_scores = True,
              content: bool = True,
              allow_selection: bool = False,
              anchors = ["<START_JSON>", "</END_JSON>"],
              verbose: bool = True, **kwargs) -> List[str]:
        """
        Find the most relevant options based on a query.
        
        Args:
            options: List of options or dictionary of options
            query: Search query to match against options
            n: Maximum number of results to return
            trials: Number of retry attempts if an error occurs
            min_score: Minimum possible score
            max_score: Maximum possible score
            threshold: Minimum score required to include in results
            model: Model to use for ranking
            context: Additional context to help with ranking
            temperature: Temperature for generation (lower = more deterministic)
            allow_selection: Whether to allow user to select files by index
            verbose: Whether to print output during generation
            
        Returns:
            List of the most relevant options
        """
        
        options_map = {int(i): option for i, option in enumerate(options)}
        if not options_map:
            return []
           
        # Format context if provided        

        output_format = f"{anchors[0]}(LIST(LIST(idx:INT, score:INT)))){anchors[1]}"

        prompt = f"""
            --PARAMS--
            GOAL={self.goal}
            QUERY={query}
            CONTEXT={str(context)}
            OPTIONS={options_map} 
            MIN_SCORE={min_score}
            MAX_SCORE={max_score}
            THRESHOLD={threshold} DO NOT PRINT THE OPTIONS IF THEY ARE NOT RELEVANT
            ANCHORS={anchors}
            N={n}
            OUTPUT_FORMAT={output_format} 
            YOU NEED TO RESPOND WITH ONLY THE OUTPUT FORMAT AND NOT in '''json bullshit as that will be harder TO PARSE
            --RESULT--     
        """
        
        print("Generated prompt for SelectOptions:", prompt, color="cyan")
        result = None  
        # Generate the response
        # Extract and parse the JSON
        for i in range(trials):
            output = ''
            response = self.model.forward( prompt, model=model, stream=True, temperature=temperature)
            for chunk in response:
                print(chunk, color="blue")
                output += chunk
            output = output.replace('(', '{').replace(')', '}')
            try:
                if anchors[0] in output:
                    json_str = output.split(anchors[0])[1].split(anchors[1])[0]
                else:
                    json_str = output
                
                if verbose:
                    print("\nParsing response...", color="cyan")
                print(f"Raw output: {json_str}", color="red")
                result = json.loads(json_str)
                break

            except json.JSONDecodeError as e:
                print(f"JSON parsing error: {e}. Retrying... ({i+1}/{trials})", color="red")
                continue
        if result is None:
            raise ValueError("Failed to parse JSON response after multiple attempts.")
        # Filter and convert to final output format
        filtered_options = []
        # incase the indexes are strings, convert to int
        for item in result:
            idx, score = item
            idx = int(idx)
            if score >= threshold and idx in options_map:
                filtered_options.append( options_map[idx])         
        print(f"Found {filtered_options} relevant options", color="green")
        results =  [option for option in filtered_options]
        return results

    def test(self):
        """
        Test the SelectFiles module with a sample query and options.
        """
        options = ['a cat', 'a dog', 'a bird']
        target_option = 'a cat'
        query = f"I want to find {target_option}"
        results = self.forward(query=query, options=options, n=1)
        assert isinstance(results, list), "Results should be a list"
        assert target_option == results[0], f"Expected '{target_option}' in results, got {results}"
        print(f"Test results: {results}", color="green")
        return {
            "success": True,
            "message": f"Test passed with results: {results}"
        }

    