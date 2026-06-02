#!/usr/bin/env python3
"""
LLM Enhancer for DSX Analyzer
Uses OpenAI GPT-4o to generate contextual transformation logic descriptions.
"""

import os
import json
import hashlib
from typing import Dict, List, Optional
from pathlib import Path

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    print("⚠️  OpenAI library not installed. Install with: pip install openai")

try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False


class TransformationEnhancer:
    """
    Enhances transformation logic descriptions using OpenAI GPT-4o.
    """
    
    def __init__(self, api_key: Optional[str] = None, model: str = "gpt-4o", use_cache: bool = True):
        """
        Initialize the enhancer.
        
        Args:
            api_key: OpenAI API key (if None, will use OPENAI_API_KEY env var)
            model: OpenAI model to use (default: gpt-4o)
            use_cache: Whether to use caching to avoid duplicate API calls
        """
        if not OPENAI_AVAILABLE:
            raise ImportError("OpenAI library not installed. Run: pip install openai")
        
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError(
                "OpenAI API key not found. Set OPENAI_API_KEY environment variable or pass api_key parameter."
            )
        
        self.client = OpenAI(api_key=self.api_key)
        self.model = model
        self.use_cache = use_cache
        
        # Set up cache directory
        self.cache_dir = Path(".llm_cache")
        if use_cache:
            self.cache_dir.mkdir(exist_ok=True)
    
    def _get_cache_key(self, transformation: Dict) -> str:
        """Generate a cache key for a transformation."""
        # Create a unique key based on transformation details
        key_data = f"{transformation.get('Target_Table')}_{transformation.get('Target_Field')}_{transformation.get('Source_Table')}_{transformation.get('Source_Field')}_{transformation.get('Transformation_Logic')}"
        return hashlib.md5(key_data.encode()).hexdigest()
    
    def _get_cached_result(self, cache_key: str) -> Optional[str]:
        """Retrieve cached result if available."""
        if not self.use_cache:
            return None
        
        cache_file = self.cache_dir / f"{cache_key}.json"
        if cache_file.exists():
            try:
                with open(cache_file, 'r') as f:
                    data = json.load(f)
                    return data.get("enhanced_logic")
            except Exception:
                return None
        return None
    
    def _cache_result(self, cache_key: str, enhanced_logic: str):
        """Cache an enhanced result."""
        if not self.use_cache:
            return
        
        cache_file = self.cache_dir / f"{cache_key}.json"
        try:
            with open(cache_file, 'w') as f:
                json.dump({"enhanced_logic": enhanced_logic}, f)
        except Exception:
            pass  # Silently fail on cache write errors
    
    def create_system_prompt(self) -> str:
        """Create the system prompt for GPT-4o."""
        return """You are an expert data engineer analyzing IBM DataStage ETL transformations.
Your task is to explain data transformations in clear, business-friendly language.

Guidelines:
- Focus on WHAT data is being moved, FROM where TO where, and WHAT operations are performed
- Be concise but informative (1-2 sentences)
- Use business-friendly language
- Explain the purpose/intent when inferable from column names
- Assume the audience is technical but not DataStage experts
- For complex expressions, explain the logic clearly

Output format: Just the description, no extra formatting or explanations."""
    
    def create_user_prompt(self, transformations: List[Dict]) -> str:
        """Create user prompt for a batch of transformations."""
        
        if len(transformations) == 1:
            # Single transformation
            t = transformations[0]
            return f"""Analyze this DataStage transformation and provide a clear, concise description.
Context: This is part of a {t.get('Stage_Type', 'Unknown')} stage named '{t.get('Stage_Name', 'Unknown')}'.
Stage Process: {t.get('Stage_Context', 'N/A')}

Transformation Details:
Target Table: {t.get('Target_Table', 'N/A')}
Target Field: {t.get('Target_Field', 'N/A')}
Source Table: {t.get('Source_Table', 'N/A')}
Source Field: {t.get('Source_Field', 'N/A')}
Derivation: {t.get('Transformation_Logic', 'N/A')}
Type: {t.get('Transformation_Type', 'N/A')}

Provide a 2-3 sentence description explaining the business logic of this transformation, considering the stage context. Be detailed."""
        
        else:
            # Batch processing
            prompt = "Analyze these DataStage transformations and provide clear, concise descriptions for each.\n"
            prompt += "Consider the stage context and process to explain the business logic.\n\n"
            prompt += "Return your response as a JSON array with one description per transformation, in the same order.\n\n"
            prompt += "Transformations:\n"
            
            for i, t in enumerate(transformations, 1):
                prompt += f"\n{i}. Stage: {t.get('Stage_Name', 'Unknown')} ({t.get('Stage_Type', 'Unknown')})\n"
                prompt += f"   Context: {t.get('Stage_Context', 'N/A')}\n"
                prompt += f"   Target: {t.get('Target_Table')}.{t.get('Target_Field')}\n"
                prompt += f"   Source: {t.get('Source_Table')}.{t.get('Source_Field')}\n"
                prompt += f"   Derivation: {t.get('Transformation_Logic', 'N/A')}\n"
            
            prompt += '\n\nReturn format: ["description 1", "description 2", ...]\n'
            prompt += "Each description should be 2-3 sentences long. Explain the transformation's purpose, the business logic involved, and how the data is being modified or routed."
            
            return prompt
    
    def enhance_transformation(self, transformation: Dict) -> str:
        """
        Enhance a single transformation description.
        
        Args:
            transformation: Dict with keys: Target_Table, Target_Field, Source_Table, 
                          Source_Field, Transformation_Logic, Transformation_Type
        
        Returns:
            Enhanced transformation description
        """
        # Check cache first
        cache_key = self._get_cache_key(transformation)
        cached = self._get_cached_result(cache_key)
        if cached:
            return cached
        
        try:
            # Call OpenAI API
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.create_system_prompt()},
                    {"role": "user", "content": self.create_user_prompt([transformation])}
                ],
                max_tokens=200,
                temperature=0.3,  # Lower temperature for more consistent outputs
                timeout=30.0
            )
            
            enhanced_logic = response.choices[0].message.content.strip()
            
            # Cache the result
            self._cache_result(cache_key, enhanced_logic)
            
            return enhanced_logic
            
        except Exception as e:
            print(f"⚠️  Error enhancing transformation: {e}")
            # Fallback to original logic
            return transformation.get('Transformation_Logic', 'N/A')
    
    def batch_enhance(self, transformations: List[Dict], batch_size: int = 50) -> List[str]:
        """
        Enhance multiple transformations in batches.
        
        Args:
            transformations: List of transformation dicts
            batch_size: Number of transformations to process per API call
        
        Returns:
            List of enhanced descriptions in the same order
        """
        if not transformations:
            return []
        
        enhanced_results = []
        
        # Determine if we should show progress bar
        show_progress = TQDM_AVAILABLE and len(transformations) > 10
        
        if show_progress:
            pbar = tqdm(total=len(transformations), desc="Enhancing with GPT-4o", unit="transformations")
        
        # Process in batches
        for i in range(0, len(transformations), batch_size):
            batch = transformations[i:i + batch_size]
            
            # Check cache for each item in batch
            batch_results = []
            items_to_process = []
            item_indices = []
            
            for idx, t in enumerate(batch):
                cache_key = self._get_cache_key(t)
                cached = self._get_cached_result(cache_key)
                
                if cached:
                    batch_results.append((idx, cached))
                else:
                    items_to_process.append(t)
                    item_indices.append(idx)
            
            # Process uncached items
            if items_to_process:
                try:
                    if len(items_to_process) == 1:
                        # Single item - use simple prompt
                        enhanced = self.enhance_transformation(items_to_process[0])
                        batch_results.append((item_indices[0], enhanced))
                    else:
                        # Multiple items - use batch prompt
                        response = self.client.chat.completions.create(
                            model=self.model,
                            messages=[
                                {"role": "system", "content": self.create_system_prompt()},
                                {"role": "user", "content": self.create_user_prompt(items_to_process)}
                            ],
                            max_tokens=200 * len(items_to_process),
                            temperature=0.3,
                            timeout=60.0
                        )
                        
                        content = response.choices[0].message.content.strip()
                        
                        # Parse JSON response
                        try:
                            descriptions = json.loads(content)
                            if isinstance(descriptions, dict):
                                # Sometimes the model wraps in an object
                                descriptions = descriptions.get('descriptions', [])
                        except json.JSONDecodeError:
                            # Fallback: try to extract array from text
                            import re
                            match = re.search(r'\[(.*?)\]', content, re.DOTALL)
                            if match:
                                try:
                                    descriptions = json.loads(f'[{match.group(1)}]')
                                except:
                                    descriptions = [t.get('Transformation_Logic', 'N/A') for t in items_to_process]
                            else:
                                descriptions = [t.get('Transformation_Logic', 'N/A') for t in items_to_process]
                        
                        # Add to results and cache
                        for idx, (orig_idx, t, desc) in enumerate(zip(item_indices, items_to_process, descriptions)):
                            batch_results.append((orig_idx, desc))
                            cache_key = self._get_cache_key(t)
                            self._cache_result(cache_key, desc)
                
                except Exception as e:
                    print(f"⚠️  Error in batch enhancement: {e}")
                    # Fallback to original logic for uncached items
                    for orig_idx, t in zip(item_indices, items_to_process):
                        batch_results.append((orig_idx, t.get('Transformation_Logic', 'N/A')))
            
            # Sort by original index and extract descriptions
            batch_results.sort(key=lambda x: x[0])
            enhanced_results.extend([desc for _, desc in batch_results])
            
            if show_progress:
                pbar.update(len(batch))
        
        if show_progress:
            pbar.close()
        
        return enhanced_results
    
    def estimate_cost(self, num_transformations: int, batch_size: int = 50) -> Dict[str, float]:
        """
        Estimate the cost of processing transformations.
        
        Args:
            num_transformations: Number of transformations to process
            batch_size: Batch size for processing
        
        Returns:
            Dict with cost estimates
        """
        # GPT-4o pricing (as of 2024)
        # ~$5.00 per 1M input tokens
        # ~$15.00 per 1M output tokens
        
        avg_input_tokens_per_transform = 80  # Estimate
        avg_output_tokens_per_transform = 50  # Estimate
        
        total_input_tokens = num_transformations * avg_input_tokens_per_transform
        total_output_tokens = num_transformations * avg_output_tokens_per_transform
        
        input_cost = (total_input_tokens / 1_000_000) * 5.00
        output_cost = (total_output_tokens / 1_000_000) * 15.00
        total_cost = input_cost + output_cost
        
        num_api_calls = (num_transformations + batch_size - 1) // batch_size
        
        return {
            "num_transformations": num_transformations,
            "num_api_calls": num_api_calls,
            "estimated_input_tokens": total_input_tokens,
            "estimated_output_tokens": total_output_tokens,
            "estimated_cost_usd": round(total_cost, 2)
        }