"""Views for the icons app."""
import logging
from rest_framework import generics, status, views
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from django.db.models import Q
from django.conf import settings

from icons.models import Icon
from icons.serializers import IconSerializer
from hub.services.llm_service import get_llm_service

logger = logging.getLogger(__name__)


class IconListView(generics.ListAPIView):
    """
    API endpoint that allows icons to be viewed and filtered.
    
    Permissions:
        - GET: Any user can view icons
    
    Query Parameters:
        - church: Filter by church ID
        - tags: Filter by tag name (can be comma-separated)
        - search: Search in title
    
    Returns:
        A paginated list of icons with their details.
    
    Example Requests:
        GET /api/icons/
        GET /api/icons/?church=1
        GET /api/icons/?tags=cross,saint
        GET /api/icons/?search=nativity
    """
    serializer_class = IconSerializer
    permission_classes = [AllowAny]
    
    def get_queryset(self):
        """Get icons with optional filtering."""
        queryset = Icon.objects.select_related('church').prefetch_related('tags')
        
        # Filter by church
        church_id = self.request.query_params.get('church')
        if church_id:
            queryset = queryset.filter(church_id=church_id)
        
        # Filter by tags
        tags = self.request.query_params.get('tags')
        if tags:
            tag_list = [tag.strip() for tag in tags.split(',')]
            for tag in tag_list:
                queryset = queryset.filter(tags__name__iexact=tag)
        
        # Search in title
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(title__icontains=search)
            )
        
        return queryset.distinct().order_by('-created_at')


class IconDetailView(generics.RetrieveAPIView):
    """
    API endpoint that allows a single icon to be viewed.
    
    Permissions:
        - GET: Any user can view icon details
    
    Returns:
        A JSON response with the icon details including:
        - id
        - title
        - church (id and name)
        - tags (list)
        - image S3 URL
        - thumbnail S3 URL
        - timestamps
    
    Example Requests:
        GET /api/icons/1/
    """
    serializer_class = IconSerializer
    permission_classes = [AllowAny]
    queryset = Icon.objects.select_related('church').prefetch_related('tags')


class IconMatchView(views.APIView):
    """
    AI-powered icon matching endpoint.
    
    Uses LLM to analyze a natural language prompt and return the most
    appropriate icon(s) based on semantic understanding.
    
    Permissions:
        - POST: Any user can request icon matching
    
    Request Body:
        {
            "prompt": "string (required) - Natural language description",
            "church_id": "integer (optional) - Limit to specific church",
            "return_format": "string (optional) - 'id' or 'full' (default: 'full')",
            "max_results": "integer (optional) - Maximum icons to return (default: 3)"
        }
    
    Response:
        {
            "matches": [
                {
                    "icon_id": 1,
                    "confidence": "high|medium|low",
                    "icon": {...}  // Full icon details if return_format='full'
                }
            ]
        }
    
    Example Requests:
        POST /api/icons/match/
        {
            "prompt": "Icon showing the nativity scene",
            "return_format": "full"
        }
    """
    permission_classes = [AllowAny]
    
    def post(self, request):
        """Handle icon matching request."""
        # Extract request parameters
        prompt = request.data.get('prompt')
        church_id = request.data.get('church_id')
        return_format = request.data.get('return_format', 'full')
        max_results = request.data.get('max_results', 3)
        
        # Validate prompt
        if not prompt:
            return Response(
                {'error': 'prompt is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate return_format
        if return_format not in ['id', 'full']:
            return Response(
                {'error': 'return_format must be "id" or "full"'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get icons to match against
        queryset = Icon.objects.select_related('church').prefetch_related('tags')
        if church_id:
            queryset = queryset.filter(church_id=church_id)
        
        icons = list(queryset)
        
        if not icons:
            return Response(
                {'matches': []},
                status=status.HTTP_200_OK
            )
        
        # Format icon data for LLM
        icon_descriptions = []
        for icon in icons:
            tags = ', '.join([tag.name for tag in icon.tags.all()])
            description = f"Icon ID: {icon.id}, Title: {icon.title}, Tags: {tags}"
            icon_descriptions.append(description)
        
        # Create LLM prompt
        system_prompt = """
You match a user's natural-language request to the most relevant icons.

INPUT:
- A list of icons. Each icon has: ID, Title, and Tags.
- A user request.
- A maximum number of results (N).

OUTPUT FORMAT (STRICT):
Return a JSON array of match objects. Each object must follow this exact format:

[
  {
    "id": 3,
    "confidence": "high"
  },
  {
    "id": 12,
    "confidence": "medium"
  }
]

Rules for Output:
- Do NOT include any text outside the JSON.
- Do NOT include extra keys or commentary.
- If no icons are meaningfully relevant, return: []
- Return at most N matches.

CONFIDENCE SCORING:
Assign confidence based on clarity of match:
- "high": The icon's title or tags clearly and directly match the request, with minimal ambiguity.
- "medium": The match is plausible and relevant, but not exact.
- "low": Only return "low" confidence if it is still clearly related; otherwise do not return it at all.

RELEVANCE RULES:
- Prefer icons whose Title strongly matches the user request.
- Next, consider strong Tag matches.
- Ignore weak or tangential keyword overlap.
- Only return IDs that appear in the provided list.
- NEVER guess or invent icons.

TIEBREAKERS:
If multiple icons seem similar in relevance:
1) Exact title match or near-synonym wins.
2) More specific tags beat general tags.
3) Well-known canonical association beats broad thematic similarity.

If unsure whether an icon is relevant:
DO NOT RETURN IT.
"""
        
        allowed_ids = [icon.id for icon in icons]

        user_message = f"""User request: "{prompt}"
Allowed icon IDs: {allowed_ids}

Available icons (ID, Title, Tags):
{chr(10).join(icon_descriptions)}

Return up to {max_results} most relevant icons as a JSON array of objects with "id" and "confidence" fields."""
        
        try:
            # Check if OpenAI API key is configured
            from openai import OpenAI
            from openai import APIError
            if not settings.OPENAI_API_KEY:
                logger.warning("OPENAI_API_KEY not configured, falling back to simple tag matching")
                # Fallback to simple tag/title matching
                matched_ids = self._simple_match_icons(icons, prompt, max_results)
                # Convert to expected format with default confidence
                matched_results = [
                    {'id': icon_id, 'confidence': 'medium'}
                    for icon_id in matched_ids
                ]
            else:
                client = OpenAI(api_key=settings.OPENAI_API_KEY)
                
                # Try models in order of preference, falling back if one fails
                models_to_try = ['gpt-5-mini', 'gpt-4.1-nano', 'gpt-4o-mini', 'gpt-4.1-mini']
                response = None
                last_error = None
                
                for model in models_to_try:
                    try:
                        # Try with temperature first
                        response = client.chat.completions.create(
                            model=model,
                            messages=[
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_message},
                            ],
                            max_completion_tokens=500
                        )
                        logger.info(f"Successfully used model: {model}")
                        break
                    except APIError as api_error:
                        last_error = api_error
                        error_body = getattr(api_error, 'body', {}) or {}
                        error_code = error_body.get('error', {}).get('code', '')
                        error_message = str(api_error)
                        
                        # Check if it's a model access error (403 or model_not_found)
                        if api_error.status_code == 403 or 'model_not_found' in error_code or 'does not have access' in error_message:
                            logger.warning(f"Model {model} not available (status: {api_error.status_code}), trying next model...")
                            continue
                        # Check if it's a temperature unsupported error
                        elif api_error.status_code == 400 and ('unsupported_value' in error_code or 'temperature' in error_message.lower()):
                            logger.warning(f"Model {model} doesn't support custom temperature, retrying without temperature parameter...")
                            try:
                                # Retry without temperature parameter (uses default)
                                response = client.chat.completions.create(
                                    model=model,
                                    messages=[
                                        {"role": "system", "content": system_prompt},
                                        {"role": "user", "content": user_message},
                                    ],
                                    max_completion_tokens=500,
                                )
                                logger.info(f"Successfully used model: {model} (without temperature)")
                                break
                            except Exception as retry_error:
                                logger.warning(f"Retry without temperature also failed for {model}, trying next model...")
                                last_error = retry_error
                                continue
                        else:
                            # For other API errors, re-raise immediately
                            raise
                    except Exception as model_error:
                        last_error = model_error
                        error_str = str(model_error)
                        # Check if it's a model access error
                        if 'model_not_found' in error_str or 'does not have access' in error_str:
                            logger.warning(f"Model {model} not available, trying next model...")
                            continue
                        # Check if it's a temperature error
                        elif 'temperature' in error_str.lower() and 'unsupported' in error_str.lower():
                            logger.warning(f"Model {model} doesn't support custom temperature, retrying without temperature parameter...")
                            try:
                                # Retry without temperature parameter
                                response = client.chat.completions.create(
                                    model=model,
                                    messages=[
                                        {"role": "system", "content": system_prompt},
                                        {"role": "user", "content": user_message},
                                    ],
                                    max_completion_tokens=500,
                                )
                                logger.info(f"Successfully used model: {model} (without temperature)")
                                break
                            except Exception as retry_error:
                                logger.warning(f"Retry without temperature also failed for {model}, trying next model...")
                                last_error = retry_error
                                continue
                        else:
                            # For other errors, re-raise immediately
                            raise
                
                if not response:
                    raise last_error if last_error else Exception("No models available")
                
                # Parse the response
                llm_response = response.choices[0].message.content.strip()
                
                # Try to parse as JSON array
                import json
                try:
                    parsed_response = json.loads(llm_response)
                    if not isinstance(parsed_response, list):
                        parsed_response = [parsed_response]
                    
                    # Handle new format: array of objects with 'id' and 'confidence'
                    matched_results = []
                    valid_confidence_levels = {'high', 'medium', 'low'}
                    for item in parsed_response:
                        if isinstance(item, dict):
                            # New format: {"id": 3, "confidence": "high"}
                            if 'id' in item:
                                confidence = item.get('confidence', 'medium')
                                # Validate confidence level
                                if confidence not in valid_confidence_levels:
                                    logger.warning(f"Invalid confidence '{confidence}', defaulting to 'medium'")
                                    confidence = 'medium'
                                matched_results.append({
                                    'id': item['id'],
                                    'confidence': confidence
                                })
                        elif isinstance(item, (int, str)):
                            # Backward compatibility: just an ID
                            matched_results.append({
                                'id': int(item),
                                'confidence': 'medium'  # Default if not provided
                            })
                    
                    # Limit to max_results
                    matched_results = matched_results[:max_results]
                    
                except json.JSONDecodeError:
                    # Fallback: extract numbers from response
                    import re
                    matched_ids = [int(x) for x in re.findall(r'\d+', llm_response)]
                    matched_results = [
                        {'id': icon_id, 'confidence': 'medium'}
                        for icon_id in matched_ids[:max_results]
                    ]
            
        except Exception as e:
            logger.error(f"Error in icon matching: {e}", exc_info=True)
            # Fallback to simple matching if LLM fails
            try:
                matched_ids = self._simple_match_icons(icons, prompt, max_results)
                # Convert to expected format with default confidence
                matched_results = [
                    {'id': icon_id, 'confidence': 'medium'}
                    for icon_id in matched_ids
                ]
            except Exception as fallback_error:
                logger.error(f"Fallback matching also failed: {fallback_error}")
                return Response(
                    {'error': 'Failed to process icon matching request'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
        
        # Build response using matched_results with confidence from LLM
        matches = []
        for match_result in matched_results:
            icon_id = match_result['id']
            confidence = match_result['confidence']
            
            try:
                icon = Icon.objects.select_related('church').prefetch_related('tags').get(id=icon_id)
                match_data = {
                    'icon_id': icon.id,
                    'confidence': confidence
                }
                
                if return_format == 'full':
                    serializer = IconSerializer(icon)
                    match_data['icon'] = serializer.data
                
                matches.append(match_data)
            except Icon.DoesNotExist:
                logger.warning(f"LLM returned non-existent icon ID: {icon_id}")
                continue
        
        return Response({
            'matches': matches
        }, status=status.HTTP_200_OK)
    
    def _simple_match_icons(self, icons, prompt, max_results):
        """Simple fallback matching based on title and tag keywords."""
        prompt_lower = prompt.lower()
        scored_icons = []
        
        for icon in icons:
            score = 0
            title_lower = icon.title.lower()
            tags_lower = [tag.name.lower() for tag in icon.tags.all()]
            
            # Check title matches
            if prompt_lower in title_lower or title_lower in prompt_lower:
                score += 10
            
            # Check tag matches
            for tag in tags_lower:
                if tag in prompt_lower or prompt_lower in tag:
                    score += 5
            
            if score > 0:
                scored_icons.append((score, icon.id))
        
        # Sort by score descending and return IDs
        scored_icons.sort(reverse=True, key=lambda x: x[0])
        return [icon_id for _, icon_id in scored_icons[:max_results]]
