"""
Centralized parsing and counting service to eliminate logic duplication.
This service provides a single source of truth for workout parsing logic.
"""
import re
from typing import Dict, List, Optional, Tuple, Union
from parsers.workout import (
    parse_weight_x_reps, extract_weights, extract_numbers, 
    align_sets, _extract_declared_sets, _extract_sets_from_bracket,
    is_data_line, is_probable_data_segment, parse_bw_weight
)
from services.helpers import get_set_stats


class WorkoutParsingService:
    """Centralized service for all workout parsing operations."""
    
    @staticmethod
    def parse_workout_text(text: str, bodyweight: Optional[float] = None) -> Dict:
        """
        Parse workout text into structured data.
        This is the single source of truth for parsing logic.
        
        Args:
            text: Raw workout text
            bodyweight: User's bodyweight for BW calculations
            
        Returns:
            Dictionary with parsed workout data
        """
        if not text or not text.strip():
            return {
                'sets_json': {'weights': [], 'reps': []},
                'top_weight': 0,
                'top_reps': 0,
                'estimated_1rm': 0,
                'set_count': 0,
                'volume': 0
            }
        
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        
        # Extract declared sets count
        declared_sets = None
        for line in lines:
            sets_count, _ = _extract_declared_sets(line)
            if sets_count:
                declared_sets = sets_count
                break
        
        # Extract sets from bracket notation
        if not declared_sets:
            for line in lines:
                bracket_sets = _extract_sets_from_bracket(line)
                if bracket_sets:
                    declared_sets = bracket_sets
                    break
        
        # Parse weights and reps
        all_weights = []
        all_reps = []
        
        for line in lines:
            if not is_data_line(line):
                continue
                
            # Try weight x reps format first
            weights, reps = parse_weight_x_reps(line, bodyweight)
            if weights and reps:
                all_weights.extend(weights)
                all_reps.extend(reps)
                continue
            
            # Extract individual numbers
            numbers = extract_numbers(line)
            if numbers:
                # Assume alternating weight/reps pattern
                for i in range(0, len(numbers) - 1, 2):
                    if i + 1 < len(numbers):
                        all_weights.append(numbers[i])
                        all_reps.append(int(numbers[i + 1]))
        
        # Align sets if we have a declared count
        if declared_sets and (all_weights or all_reps):
            all_weights, all_reps = align_sets(all_weights, all_reps, declared_sets)
        
        # Calculate metrics
        sets_json = {'weights': all_weights, 'reps': all_reps}
        top_weight = max(all_weights) if all_weights else 0
        top_reps = 0
        
        if all_weights and all_reps:
            # Find reps for the top weight
            try:
                top_weight_idx = all_weights.index(top_weight)
                top_reps = all_reps[top_weight_idx] if top_weight_idx < len(all_reps) else 0
            except (ValueError, IndexError):
                top_reps = max(all_reps) if all_reps else 0
        
        # Calculate 1RM and volume
        estimated_1rm, _, volume = get_set_stats(sets_json)
        
        return {
            'sets_json': sets_json,
            'top_weight': top_weight,
            'top_reps': top_reps,
            'estimated_1rm': estimated_1rm,
            'set_count': len(all_weights),
            'volume': volume
        }
    
    @staticmethod
    def count_sets_from_text(text: str) -> int:
        """
        Count the number of sets from workout text.
        Uses the same parsing logic as parse_workout_text for consistency.
        
        Args:
            text: Raw workout text
            
        Returns:
            Number of sets
        """
        parsed = WorkoutParsingService.parse_workout_text(text)
        return parsed['set_count']
    
    @staticmethod
    def validate_workout_text(text: str) -> Tuple[bool, str]:
        """
        Validate workout text and return validation result.
        
        Args:
            text: Raw workout text
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        if not text or not text.strip():
            return False, "Workout text cannot be empty"
        
        try:
            parsed = WorkoutParsingService.parse_workout_text(text)
            
            if parsed['set_count'] == 0:
                return False, "No valid sets found in workout text"
            
            if not parsed['sets_json']['weights'] and not parsed['sets_json']['reps']:
                return False, "No weights or reps found"
            
            return True, ""
            
        except Exception as e:
            return False, f"Parsing error: {str(e)}"
    
    @staticmethod
    def format_sets_display(sets_json: Dict) -> str:
        """
        Format sets data for display.
        Provides consistent formatting across the application.
        
        Args:
            sets_json: Dictionary with weights and reps arrays
            
        Returns:
            Formatted string representation
        """
        if not sets_json or not isinstance(sets_json, dict):
            return ""
        
        weights = sets_json.get('weights', [])
        reps = sets_json.get('reps', [])
        
        if not weights and not reps:
            return ""
        
        # Ensure equal length
        max_len = max(len(weights), len(reps))
        if len(weights) < max_len:
            weights.extend([weights[-1] if weights else 0] * (max_len - len(weights)))
        if len(reps) < max_len:
            reps.extend([reps[-1] if reps else 0] * (max_len - len(reps)))
        
        # Format as "weight x reps" pairs
        pairs = []
        for w, r in zip(weights, reps):
            if w == int(w):  # Display as integer if no decimal
                pairs.append(f"{int(w)} x {r}")
            else:
                pairs.append(f"{w:.1f} x {r}")
        
        return ", ".join(pairs)
    
    @staticmethod
    def calculate_1rm(weight: float, reps: int) -> float:
        """
        Calculate estimated 1RM using Epley formula.
        Centralized calculation to ensure consistency.
        
        Args:
            weight: Weight lifted
            reps: Number of repetitions
            
        Returns:
            Estimated 1RM
        """
        if weight <= 0 or reps <= 0:
            return 0
        
        if reps == 1:
            return weight
        
        # Epley formula: 1RM = weight * (1 + reps/30)
        return weight * (1 + reps / 30)
    
    @staticmethod
    def is_bodyweight_exercise(exercise_name: str, exercise_text: str = "") -> bool:
        """
        Determine if an exercise uses bodyweight.
        
        Args:
            exercise_name: Name of the exercise
            exercise_text: Exercise text content
            
        Returns:
            True if exercise uses bodyweight
        """
        from list_of_exercise import BW_EXERCISES
        
        if exercise_name in BW_EXERCISES:
            return True
        
        # Check for BW indicators in text
        text_lower = (exercise_text or "").lower()
        return 'bw' in text_lower or 'bodyweight' in text_lower


# Global instance for easy access
parsing_service = WorkoutParsingService()