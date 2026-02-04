"""
Workout Quality Assessment System

This module provides a comprehensive scoring system that evaluates workout quality
beyond just peak 1RM, considering volume, consistency, and overall performance.
"""
from typing import Dict, List, Tuple, Optional
from datetime import datetime
import math
import statistics


class WorkoutQualityScorer:
    """
    Advanced workout quality scoring system that considers:
    1. Peak 1RM (strength)
    2. Volume consistency (total work done)
    3. Set completion rate (how many sets in target range)
    4. Progressive overload trend
    """
    
    @staticmethod
    def calculate_workout_score(sets_json: Dict, target_rep_range: Tuple[int, int] = None) -> Dict:
        """
        Calculate comprehensive workout quality score.
        
        Args:
            sets_json: Dictionary with 'weights' and 'reps' arrays
            target_rep_range: Tuple of (min_reps, max_reps) for the exercise
            
        Returns:
            Dictionary with various quality metrics
        """
        if not sets_json or not isinstance(sets_json, dict):
            return WorkoutQualityScorer._empty_score()
        
        weights = sets_json.get('weights', [])
        reps = sets_json.get('reps', [])
        
        if not weights or not reps:
            return WorkoutQualityScorer._empty_score()
        
        weights = list(weights)
        reps = list(reps)
        if len(weights) != len(reps):
            if len(weights) < len(reps) and weights:
                weights = weights + [weights[-1]] * (len(reps) - len(weights))
            elif len(reps) < len(weights) and reps:
                reps = reps + [reps[-1]] * (len(weights) - len(reps))
        
        if not weights or not reps:
            return WorkoutQualityScorer._empty_score()
        
        set_scores = []
        total_volume = 0.0
        peak_1rm = 0.0

        for w, r in zip(weights, reps):
            try:
                weight = float(w)
                rep_count = int(r)

                if weight <= 0 or rep_count <= 0:
                    continue

                set_1rm = WorkoutQualityScorer._estimate_1rm(weight, rep_count)
                set_volume = weight * rep_count

                set_scores.append({
                    'weight': weight,
                    'reps': rep_count,
                    '1rm': set_1rm,
                    'volume': set_volume,
                })

                total_volume += set_volume
                peak_1rm = max(peak_1rm, set_1rm)

            except (ValueError, TypeError):
                continue
        
        if not set_scores:
            return WorkoutQualityScorer._empty_score()
        
        weights_sum = 0.0
        weighted_1rm_sum = 0.0
        weighted_adherence_sum = 0.0
        effective_volume = 0.0
        working_set_count = 0

        for s in set_scores:
            intensity_ratio = (s['1rm'] / peak_1rm) if peak_1rm > 0 else 0.0
            importance = WorkoutQualityScorer._working_set_weight(intensity_ratio)
            adherence = WorkoutQualityScorer._rep_adherence_score(s['reps'], target_rep_range, intensity_ratio)
            quality = WorkoutQualityScorer._calculate_set_quality(importance, adherence)

            s['intensity_ratio'] = intensity_ratio
            s['importance'] = importance
            s['adherence'] = adherence
            s['quality'] = quality

            if importance > 0.15:
                working_set_count += 1

            weights_sum += importance
            weighted_1rm_sum += s['1rm'] * importance
            weighted_adherence_sum += adherence * importance
            effective_volume += s['volume'] * importance

        if weights_sum > 0:
            avg_1rm = weighted_1rm_sum / weights_sum
            rep_range_adherence = weighted_adherence_sum / weights_sum
        else:
            avg_1rm = statistics.mean([s['1rm'] for s in set_scores])
            rep_range_adherence = WorkoutQualityScorer._calculate_rep_range_adherence(set_scores, target_rep_range)

        volume_consistency = WorkoutQualityScorer._calculate_volume_consistency(set_scores)
        
        # Calculate overall workout quality score (0-100)
        quality_index = WorkoutQualityScorer._calculate_overall_quality(
            peak_1rm,
            avg_1rm,
            effective_volume,
            volume_consistency,
            rep_range_adherence,
            working_set_count,
            len(set_scores),
        )
        quality_score = quality_index * 100.0
        
        return {
            'peak_1rm': peak_1rm,
            'avg_1rm': avg_1rm,
            'total_volume': total_volume,
            'effective_volume': effective_volume,
            'set_count': len(set_scores),
            'working_set_count': working_set_count,
            'sustained_intensity': (avg_1rm / peak_1rm) if peak_1rm > 0 else 0,
            'volume_consistency': volume_consistency,
            'rep_range_adherence': rep_range_adherence,
            'quality_index': quality_index,
            'quality_score': quality_score,
            'set_details': set_scores
        }
    
    @staticmethod
    def _calculate_set_quality(importance: float, adherence: float) -> float:
        if importance <= 0:
            return 0.0
        score = 0.2 + 0.55 * importance + 0.25 * adherence
        return max(0.0, min(1.0, score))

    @staticmethod
    def _estimate_1rm(weight: float, reps: int) -> float:
        if weight <= 0 or reps <= 0:
            return 0.0
        if reps <= 12:
            return weight * (1.0 + reps / 30.0)
        return weight * (float(reps) ** 0.10)

    @staticmethod
    def _working_set_weight(intensity_ratio: float) -> float:
        if intensity_ratio <= 0:
            return 0.0
        x = (intensity_ratio - 0.60) / 0.40
        x = max(0.0, min(1.0, x))
        return x * x

    @staticmethod
    def _rep_adherence_score(reps: int, target_range: Tuple[int, int] = None, intensity_ratio: float = 0.0) -> float:
        if not target_range:
            return 1.0
        if reps <= 0:
            return 0.0

        min_reps, max_reps = target_range
        if min_reps <= reps <= max_reps:
            return 1.0

        if intensity_ratio >= 0.90:
            return 1.0

        if reps > max_reps and max_reps > 0:
            over = reps - max_reps
            return max(0.0, 1.0 - (over / max_reps))
        if reps < min_reps and min_reps > 0:
            under = min_reps - reps
            return max(0.0, 1.0 - (under / min_reps))
        return 0.0
    
    @staticmethod
    def _calculate_volume_consistency(set_scores: List[Dict]) -> float:
        if len(set_scores) < 2:
            return 1.0

        working = [s for s in set_scores if s.get('importance', 1.0) > 0.15]
        if len(working) < 2:
            working = set_scores

        volumes = [float(s.get('volume') or 0.0) for s in working]
        if not volumes or max(volumes) <= 0:
            return 0.0

        y = [math.log(v + 1.0) for v in volumes]
        n = len(y)
        if n < 2:
            return 1.0

        x = list(range(n))
        x_mean = statistics.mean(x)
        y_mean = statistics.mean(y)
        denom = sum((xi - x_mean) ** 2 for xi in x)
        if denom <= 0:
            std_y = statistics.stdev(y) if n > 1 else 0.0
            return math.exp(-(std_y / 0.35))

        slope = sum((x[i] - x_mean) * (y[i] - y_mean) for i in range(n)) / denom
        intercept = y_mean - slope * x_mean
        residuals = [y[i] - (slope * x[i] + intercept) for i in range(n)]
        std_res = statistics.stdev(residuals) if n > 1 else 0.0
        return math.exp(-(std_res / 0.35))
    
    @staticmethod
    def _calculate_rep_range_adherence(set_scores: List[Dict], target_range: Tuple[int, int] = None) -> float:
        if not target_range or not set_scores:
            return 1.0
        scores = []
        for s in set_scores:
            reps = int(s.get('reps') or 0)
            intensity_ratio = float(s.get('intensity_ratio') or 0.0)
            scores.append(WorkoutQualityScorer._rep_adherence_score(reps, target_range, intensity_ratio))
        if not scores:
            return 0.0
        return sum(scores) / len(scores)
    
    @staticmethod
    def _calculate_overall_quality(
        peak_1rm: float,
        avg_1rm: float,
        effective_volume: float,
        volume_consistency: float,
        rep_range_adherence: float,
        working_set_count: int,
        total_set_count: int,
    ) -> float:
        if peak_1rm <= 0:
            return 0.0

        sustained = (avg_1rm / peak_1rm) if peak_1rm > 0 else 0.0
        sustained = max(0.0, min(1.0, sustained))

        work_density = (working_set_count / total_set_count) if total_set_count > 0 else 0.0
        work_density = max(0.0, min(1.0, work_density))

        consistency = max(0.0, min(1.0, volume_consistency))
        adherence = max(0.0, min(1.0, rep_range_adherence))

        quality_index = (
            0.40 * consistency +
            0.35 * adherence +
            0.15 * sustained +
            0.10 * work_density
        )
        return max(0.0, min(1.0, quality_index))
    
    @staticmethod
    def _empty_score() -> Dict:
        """Return empty score for invalid inputs."""
        return {
            'peak_1rm': 0,
            'avg_1rm': 0,
            'total_volume': 0,
            'effective_volume': 0,
            'set_count': 0,
            'working_set_count': 0,
            'sustained_intensity': 0,
            'volume_consistency': 0,
            'rep_range_adherence': 0,
            'quality_index': 0,
            'quality_score': 0,
            'set_details': []
        }
    
    @staticmethod
    def compare_workouts(workout1_sets: Dict, workout2_sets: Dict, 
                        target_rep_range: Tuple[int, int] = None) -> Dict:
        """
        Compare two workouts and determine which is better overall.
        
        Returns:
            Dictionary with comparison results and recommendation
        """
        score1 = WorkoutQualityScorer.calculate_workout_score(workout1_sets, target_rep_range)
        score2 = WorkoutQualityScorer.calculate_workout_score(workout2_sets, target_rep_range)
        
        # Determine winner based on quality score
        if score1['quality_score'] > score2['quality_score']:
            winner = 1
            difference = score1['quality_score'] - score2['quality_score']
        elif score2['quality_score'] > score1['quality_score']:
            winner = 2
            difference = score2['quality_score'] - score1['quality_score']
        else:
            winner = 0  # Tie
            difference = 0
        
        return {
            'workout1_score': score1,
            'workout2_score': score2,
            'winner': winner,
            'score_difference': difference,
            'recommendation': WorkoutQualityScorer._generate_recommendation(score1, score2, winner)
        }
    
    @staticmethod
    def _generate_recommendation(score1: Dict, score2: Dict, winner: int) -> str:
        """Generate human-readable recommendation."""
        if winner == 0:
            return "Both workouts are equally effective."
        
        better_score = score1 if winner == 1 else score2
        worse_score = score2 if winner == 1 else score1
        workout_name = "first" if winner == 1 else "second"
        
        reasons = []
        
        if better_score['peak_1rm'] > worse_score['peak_1rm']:
            reasons.append("higher peak strength")
        
        if better_score['total_volume'] > worse_score['total_volume']:
            reasons.append("greater total volume")
        
        if better_score['volume_consistency'] > worse_score['volume_consistency']:
            reasons.append("more consistent performance")
        
        if better_score['rep_range_adherence'] > worse_score['rep_range_adherence']:
            reasons.append("better rep range adherence")
        
        if not reasons:
            reasons.append("overall better execution")
        
        return f"The {workout_name} workout is better due to {', '.join(reasons)}."