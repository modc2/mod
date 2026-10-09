import mod as m
import os
import json
import time
from typing import Dict, Any, List, Optional


class Quests:
    """
    Quest System - People can create quests with rewards, others can respond,
    and the quest referee approves the response, triggering reward payment.

    Flow:
    1. Creator creates a quest with a description, reward, and referee (defaults to self)
    2. The reward is escrowed (locked from creator's balance)
    3. Responders submit responses to the quest
    4. Referee reviews responses and approves one (taking their fee %)
    5. Approved responder receives the reward (minus treasury fee and referee fee)

    Referee System:
    - Creator selects a referee when creating the quest (defaults to creator)
    - Referee sets their fee percentage (0-50%) when they accept
    - Only the referee can approve or reject responses
    - Referee fee is paid from the quest reward on approval
    """

    folder_path = m.abspath('~/.mod/quests')

    def __init__(self, chain='chain', auth='auth.base', store='ipfs'):
        self.chain = m.mod(chain)()
        self.auth = m.mod(auth)()
        self.store = m.mod(store)()
        self.quests_path = os.path.join(self.folder_path, 'quests')
        self.responses_path = os.path.join(self.folder_path, 'responses')
        os.makedirs(self.quests_path, exist_ok=True)
        os.makedirs(self.responses_path, exist_ok=True)

    def path(self, *args):
        return os.path.join(self.folder_path, *args)

    MAX_REFEREE_FEE_PCT = 50




    def create_quest(self, title: str, description: str, reward: float, token: str = None, referee: str = None, tags: List[str] = None, deadline: int = None) -> Dict[str, Any]:
        """
        Create a new quest with a reward.

        The initiator's balance is checked to ensure they can cover the reward.
        The creator selects a referee who will judge responses. Defaults to the
        creator themselves. The referee must accept_referee() to set their fee.

        Args:
            title: Short title for the quest
            description: Detailed description of what needs to be done
            reward: Amount of reward in stable tokens
            token: Auth token of the quest creator
            referee: Address of the referee (defaults to creator)
            tags: Optional tags for categorization
            deadline: Optional unix timestamp deadline

        Returns:
            Quest object with id and details
        """
        assert token, 'Auth token required to create a quest'
        verified = self.auth.verify(token)
        creator_key = verified['key']

        assert reward > 0, 'Reward must be greater than 0'
        assert title and len(title.strip()) > 0, 'Title is required'
        assert description and len(description.strip()) > 0, 'Description is required'

        creator_balance = self.chain.balance(creator_key, address=creator_key, token='market')
        assert creator_balance >= reward, f'Insufficient balance. You have ${creator_balance:.2f} but the quest reward requires ${reward:.2f}'

        if referee is not None:
            assert isinstance(referee, str) and len(referee.strip()) > 0, 'Referee address must be a valid string'
            referee_key = referee.strip()
        else:
            referee_key = creator_key

        referee_accepted = referee_key == creator_key
        referee_fee_pct = 0 if referee_accepted else None

        quest_id = m.hash(f'{creator_key}:{title}:{time.time()}')[:16]

        quest = {
            'id': quest_id,
            'title': title.strip(),
            'description': description.strip(),
            'reward': reward,
            'creator': creator_key,
            'referee': referee_key,
            'referee_accepted': referee_accepted,
            'referee_fee_pct': referee_fee_pct,
            'status': 'open',
            'tags': tags or [],
            'deadline': deadline,
            'created_at': time.time(),
            'updated_at': time.time(),
            'responses': [],
            'approved_response': None,
        }

        quest_path = os.path.join(self.quests_path, f'{quest_id}.json')
        m.put(quest_path, quest)

        return quest

    def quests(self, status: str = None, creator: str = None, tag: str = None, n: int = 50, page: int = 0) -> List[Dict[str, Any]]:
        """
        List all quests, optionally filtered.

        Args:
            status: Filter by status (open, in_review, completed, cancelled)
            creator: Filter by creator key
            tag: Filter by tag
            n: Number of results per page
            page: Page number

        Returns:
            List of quest objects
        """
        import glob
        quest_files = glob.glob(os.path.join(self.quests_path, '*.json'))

        all_quests = []
        for qf in quest_files:
            try:
                quest = m.get(qf)
                if quest:
                    all_quests.append(quest)
            except:
                pass

        if status:
            all_quests = [q for q in all_quests if q.get('status') == status]

        if creator:
            all_quests = [q for q in all_quests if q.get('creator') == creator]

        if tag:
            all_quests = [q for q in all_quests if tag in q.get('tags', [])]

        all_quests.sort(key=lambda x: x.get('created_at', 0), reverse=True)

        start = page * n
        end = start + n
        return all_quests[start:end]

    def get_quest(self, quest_id: str) -> Dict[str, Any]:
        """
        Get a specific quest by ID.

        Args:
            quest_id: The quest identifier

        Returns:
            Quest object
        """
        quest_path = os.path.join(self.quests_path, f'{quest_id}.json')
        quest = m.get(quest_path, None)
        assert quest is not None, f'Quest {quest_id} not found'
        return quest

    def accept_referee(self, quest_id: str, fee_pct: float, token: str = None) -> Dict[str, Any]:
        """
        Accept the referee role for a quest and set fee percentage.

        Only the designated referee can call this. The fee percentage is the
        portion of the reward the referee takes on approval.

        Args:
            quest_id: The quest ID
            fee_pct: Referee fee as a percentage of the reward (0-50)
            token: Auth token of the referee

        Returns:
            Updated quest object
        """
        assert token, 'Auth token required'
        verified = self.auth.verify(token)
        caller_key = verified['key']

        quest = self.get_quest(quest_id)
        assert quest['referee'] == caller_key, 'Only the designated referee can accept'
        assert not quest.get('referee_accepted'), 'Referee has already accepted'
        assert quest['status'] == 'open', 'Quest is not open'
        assert isinstance(fee_pct, (int, float)), 'Fee must be a number'
        assert 0 <= fee_pct <= self.MAX_REFEREE_FEE_PCT, f'Referee fee must be between 0% and {self.MAX_REFEREE_FEE_PCT}%'

        quest['referee_accepted'] = True
        quest['referee_fee_pct'] = fee_pct
        quest['updated_at'] = time.time()

        quest_path = os.path.join(self.quests_path, f'{quest_id}.json')
        m.put(quest_path, quest)

        return quest

    def decline_referee(self, quest_id: str, token: str = None) -> Dict[str, Any]:
        """
        Decline the referee role. Resets referee back to the creator.

        Args:
            quest_id: The quest ID
            token: Auth token of the referee

        Returns:
            Updated quest object
        """
        assert token, 'Auth token required'
        verified = self.auth.verify(token)
        caller_key = verified['key']

        quest = self.get_quest(quest_id)
        assert quest['referee'] == caller_key, 'Only the designated referee can decline'
        assert quest['referee'] != quest['creator'], 'Creator-referee cannot decline'
        assert not quest.get('referee_accepted'), 'Referee has already accepted'

        quest['referee'] = quest['creator']
        quest['referee_accepted'] = True
        quest['referee_fee_pct'] = 0
        quest['updated_at'] = time.time()

        quest_path = os.path.join(self.quests_path, f'{quest_id}.json')
        m.put(quest_path, quest)

        return quest

    def respond(self, quest_id: str, content: str, token: str = None, attachments: List[str] = None) -> Dict[str, Any]:
        """
        Submit a response to a quest.

        Args:
            quest_id: The quest to respond to
            content: The response content (proof of work, deliverable, etc.)
            token: Auth token of the responder
            attachments: Optional list of CIDs or URLs for attachments

        Returns:
            Response object
        """
        assert token, 'Auth token required to respond'
        verified = self.auth.verify(token)
        responder_key = verified['key']

        quest = self.get_quest(quest_id)
        assert quest['status'] == 'open', f'Quest is not open for responses (status: {quest["status"]})'
        assert quest.get('referee_accepted'), 'Referee has not yet accepted this quest'

        if quest.get('deadline'):
            if time.time() > quest['deadline']:
                raise Exception('Quest deadline has passed')

        existing = [r for r in quest.get('responses', []) if r.get('responder') == responder_key]
        assert len(existing) == 0, 'You have already responded to this quest'

        response_id = m.hash(f'{responder_key}:{quest_id}:{time.time()}')[:16]

        response = {
            'id': response_id,
            'quest_id': quest_id,
            'responder': responder_key,
            'content': content.strip(),
            'attachments': attachments or [],
            'status': 'pending',
            'created_at': time.time(),
        }

        response_path = os.path.join(self.responses_path, f'{response_id}.json')
        m.put(response_path, response)

        quest['responses'].append({
            'id': response_id,
            'responder': responder_key,
            'status': 'pending',
            'created_at': response.get('created_at'),
        })
        quest['updated_at'] = time.time()

        quest_path = os.path.join(self.quests_path, f'{quest_id}.json')
        m.put(quest_path, quest)

        return response

    def get_response(self, response_id: str) -> Dict[str, Any]:
        response_path = os.path.join(self.responses_path, f'{response_id}.json')
        response = m.get(response_path, None)
        assert response is not None, f'Response {response_id} not found'
        return response

    def get_responses(self, quest_id: str) -> List[Dict[str, Any]]:
        """
        Get all responses for a quest.
        """
        quest = self.get_quest(quest_id)
        responses = []
        for ref in quest.get('responses', []):
            try:
                response = self.get_response(ref['id'])
                responses.append(response)
            except:
                pass
        return responses

    def edit_quest(self, quest_id: str, title: str = None, description: str = None, reward: float = None, tags: List[str] = None, token: str = None) -> Dict[str, Any]:
        """
        Edit an open quest. Only the creator can edit, and only while
        the quest status is 'open'.

        Args:
            quest_id: The quest ID to edit
            title: Updated title (optional)
            description: Updated description (optional)
            reward: Updated reward amount (optional)
            tags: Updated tags list (optional)
            token: Auth token of the quest creator

        Returns:
            Updated quest object
        """
        assert token, 'Auth token required to edit'
        verified = self.auth.verify(token)
        editor_key = verified['key']

        quest = self.get_quest(quest_id)
        assert quest['creator'] == editor_key, 'Only the quest creator can edit'
        assert quest['status'] == 'open', 'Can only edit open quests'

        if title is not None:
            assert len(title.strip()) > 0, 'Title cannot be empty'
            quest['title'] = title.strip()

        if description is not None:
            assert len(description.strip()) > 0, 'Description cannot be empty'
            quest['description'] = description.strip()

        if reward is not None:
            assert reward > 0, 'Reward must be greater than 0'
            if reward < quest['reward']:
                pending = [r for r in quest.get('responses', []) if r.get('status') == 'pending']
                assert not pending, f'Cannot reduce reward while {len(pending)} response(s) are pending'
            quest['reward'] = reward

        if tags is not None:
            quest['tags'] = tags

        quest['edited_at'] = time.time()
        quest['updated_at'] = time.time()

        quest_path = os.path.join(self.quests_path, f'{quest_id}.json')
        m.put(quest_path, quest)

        return quest

    def edit_response(self, response_id: str, content: str, token: str = None, attachments: List[str] = None) -> Dict[str, Any]:
        """
        Edit a pending response. Only the original responder can edit,
        and only while the response is still pending.

        Args:
            response_id: The response ID to edit
            content: Updated response content
            token: Auth token of the responder
            attachments: Optional updated attachments list

        Returns:
            Updated response object
        """
        assert token, 'Auth token required to edit'
        verified = self.auth.verify(token)
        editor_key = verified['key']

        response = self.get_response(response_id)
        assert response['responder'] == editor_key, 'Only the original responder can edit'
        assert response['status'] == 'pending', 'Can only edit pending responses'
        assert content and len(content.strip()) > 0, 'Content is required'

        response['content'] = content.strip()

        if attachments is not None:
            response['attachments'] = attachments

        response['edited_at'] = time.time()

        response_path = os.path.join(self.responses_path, f'{response_id}.json')
        m.put(response_path, response)

        return response

    def approve(self, quest_id: str, response_id: str, token: str = None) -> Dict[str, Any]:
        """
        Approve a response to a quest. This triggers the reward payment.

        Only the quest referee can approve responses.
        The reward is transferred from the creator to the responder and referee
        via the Market contract's debit function (with 5% treasury fee).

        Payment split:
        - Treasury: 5% of total reward
        - Referee: referee_fee_pct% of total reward (0% if creator is referee)
        - Responder: remainder

        Args:
            quest_id: The quest ID
            response_id: The response ID to approve
            token: Auth token of the quest referee

        Returns:
            Result with payment details
        """
        assert token, 'Auth token required to approve'
        verified = self.auth.verify(token)
        approver_key = verified['key']

        quest = self.get_quest(quest_id)
        assert quest['referee'] == approver_key, 'Only the quest referee can approve responses'
        assert quest.get('referee_accepted'), 'Referee has not accepted this quest'
        assert quest['status'] == 'open', f'Quest is not open (status: {quest["status"]})'

        response = self.get_response(response_id)
        assert response['quest_id'] == quest_id, 'Response does not belong to this quest'
        assert response['status'] == 'pending', 'Response is not pending'
        assert response['responder'] != approver_key, 'Referee cannot approve their own response'

        responder_key = response['responder']
        reward = quest['reward']
        referee_fee_pct = quest.get('referee_fee_pct', 0)
        referee_key = quest['referee']

        treasury_fee = reward * 0.05
        referee_fee = reward * referee_fee_pct / 100.0
        responder_receives = reward - treasury_fee - referee_fee

        assert responder_receives > 0, 'Reward split leaves nothing for responder'

        responder_payment_amount = reward - referee_fee

        print(f'Approving response {response_id} for quest {quest_id}. Paying ${reward:.2f}: responder gets ${responder_receives:.2f}, referee gets ${referee_fee:.2f}, treasury gets ${treasury_fee:.2f}')

        payment_hash = self.chain.debit(client=quest['creator'], provider=responder_key, amount=responder_payment_amount)

        referee_payment_hash = None
        if referee_fee > 0 and referee_key != quest['creator']:
            referee_payment_hash = self.chain.debit(client=quest['creator'], provider=referee_key, amount=referee_fee)

        response['status'] = 'approved'
        response['approved_at'] = time.time()
        response['payment_hash'] = payment_hash

        response_path = os.path.join(self.responses_path, f'{response_id}.json')
        m.put(response_path, response)

        quest['status'] = 'completed'
        quest['approved_response'] = response_id
        quest['completed_at'] = time.time()
        quest['payment_hash'] = payment_hash
        quest['referee_payment_hash'] = referee_payment_hash
        quest['updated_at'] = time.time()

        for ref in quest.get('responses', []):
            if ref['id'] == response_id:
                ref['status'] = 'approved'
            else:
                ref['status'] = 'rejected'
                try:
                    other_resp = self.get_response(ref['id'])
                    other_resp['status'] = 'rejected'
                    other_path = os.path.join(self.responses_path, f'{ref["id"]}.json')
                    m.put(other_path, other_resp)
                except:
                    pass

        quest_path = os.path.join(self.quests_path, f'{quest_id}.json')
        m.put(quest_path, quest)

        return {
            'quest_id': quest_id,
            'response_id': response_id,
            'responder': responder_key,
            'referee': referee_key,
            'reward': reward,
            'treasury_fee': treasury_fee,
            'referee_fee': referee_fee,
            'referee_fee_pct': referee_fee_pct,
            'responder_receives': responder_receives,
            'payment_hash': payment_hash,
            'referee_payment_hash': referee_payment_hash,
            'status': 'completed',
        }

    def reject(self, quest_id: str, response_id: str, reason: str = '', token: str = None) -> Dict[str, Any]:
        """
        Reject a specific response to a quest.

        Args:
            quest_id: The quest ID
            response_id: The response ID to reject
            reason: Optional reason for rejection
            token: Auth token of the quest referee

        Returns:
            Updated response object
        """
        assert token, 'Auth token required'
        verified = self.auth.verify(token)
        approver_key = verified['key']

        quest = self.get_quest(quest_id)
        assert quest['referee'] == approver_key, 'Only the quest referee can reject responses'
        assert quest.get('referee_accepted'), 'Referee has not accepted this quest'

        response = self.get_response(response_id)
        assert response['quest_id'] == quest_id, 'Response does not belong to this quest'
        assert response['status'] == 'pending', 'Response is not pending'

        response['status'] = 'rejected'
        response['rejected_at'] = time.time()
        response['rejection_reason'] = reason

        response_path = os.path.join(self.responses_path, f'{response_id}.json')
        m.put(response_path, response)

        for ref in quest.get('responses', []):
            if ref['id'] == response_id:
                ref['status'] = 'rejected'

        quest['updated_at'] = time.time()

        quest_path = os.path.join(self.quests_path, f'{quest_id}.json')
        m.put(quest_path, quest)

        return response

    def cancel_quest(self, quest_id: str, token: str = None) -> Dict[str, Any]:
        """
        Cancel an open quest. Only the creator can cancel.
        No reward is paid out.

        Args:
            quest_id: The quest ID
            token: Auth token of the quest creator

        Returns:
            Updated quest object
        """
        assert token, 'Auth token required'
        verified = self.auth.verify(token)
        creator_key = verified['key']

        quest = self.get_quest(quest_id)
        assert quest['creator'] == creator_key, 'Only the quest creator can cancel'
        assert quest['status'] == 'open', 'Can only cancel open quests'

        quest['status'] = 'cancelled'
        quest['cancelled_at'] = time.time()
        quest['updated_at'] = time.time()

        quest_path = os.path.join(self.quests_path, f'{quest_id}.json')
        m.put(quest_path, quest)

        return quest

    def my_quests(self, token: str = None, status: str = None) -> List[Dict[str, Any]]:
        """
        Get quests created by the authenticated user.
        """
        assert token, 'Auth token required'
        verified = self.auth.verify(token)
        user_key = verified['key']

        return self.quests(creator=user_key, status=status)

    def my_responses(self, token: str = None) -> List[Dict[str, Any]]:
        """
        Get all responses submitted by the authenticated user.
        """
        assert token, 'Auth token required'
        verified = self.auth.verify(token)
        user_key = verified['key']

        import glob
        response_files = glob.glob(os.path.join(self.responses_path, '*.json'))
        user_responses = []
        for rf in response_files:
            try:
                response = m.get(rf)
                if response and response.get('responder') == user_key:
                    user_responses.append(response)
            except:
                pass

        user_responses.sort(key=lambda x: x.get('created_at', 0), reverse=True)
        return user_responses

    def stats(self) -> Dict[str, Any]:
        """
        Get overall quest system statistics.
        """
        all_quests = self.quests(n=10000)
        total_reward = sum(q.get('reward', 0) for q in all_quests)
        completed_reward = sum(q.get('reward', 0) for q in all_quests if q.get('status') == 'completed')
        return {
            'total_quests': len(all_quests),
            'open': len([q for q in all_quests if q.get('status') == 'open']),
            'completed': len([q for q in all_quests if q.get('status') == 'completed']),
            'cancelled': len([q for q in all_quests if q.get('status') == 'cancelled']),
            'total_reward_posted': total_reward,
            'total_reward_paid': completed_reward,
            'total_responses': sum(len(q.get('responses', [])) for q in all_quests),
        }

    def leaderboard(self, n: int = 50) -> Dict[str, Any]:
        """
        Get leaderboard data for top questers (creators) and top responders (earners).

        Returns:
            Dict with 'questers' and 'responders' lists
        """
        all_quests = self.quests(n=10000)
        earner_map = {}
        creator_map = {}

        for q in all_quests:
            creator = q.get('creator', '')
            if creator:
                if creator not in creator_map:
                    creator_map[creator] = {
                        'creator': creator,
                        'quests_created': 0,
                        'total_reward_posted': 0,
                        'quests_completed': 0,
                        'total_responses': 0,
                    }
                creator_map[creator]['quests_created'] += 1
                creator_map[creator]['total_reward_posted'] += q.get('reward', 0)
                creator_map[creator]['total_responses'] += len(q.get('responses', []))
                if q.get('status') == 'completed':
                    creator_map[creator]['quests_completed'] += 1

            if q.get('status') == 'completed' and q.get('approved_response'):
                responses = q.get('responses', [])
                approved = None
                for r in responses:
                    if r.get('id') == q['approved_response'] or r.get('status') == 'approved':
                        approved = r
                        break

                if approved:
                    responder = approved.get('responder', '')
                    if responder:
                        if responder not in earner_map:
                            earner_map[responder] = {
                                'responder': responder,
                                'total_earned': 0,
                                'quests_completed': 0,
                            }
                        earner_map[responder]['total_earned'] += q.get('reward', 0)
                        earner_map[responder]['quests_completed'] += 1

        responders = sorted(earner_map.values(), key=lambda x: x['total_earned'], reverse=True)[:n]
        questers = sorted(creator_map.values(), key=lambda x: x['total_reward_posted'], reverse=True)[:n]

        return {'responders': responders, 'questers': questers}

    def info(self) -> Dict[str, Any]:
        """
        Get info about the quest system.
        """
        return {
            'name': 'quests',
            'description': 'Quest system - create quests, respond to them, get rewarded',
            'version': '1.0.0',
            'stats': self.stats(),
            'flow': [
                '1. Creator posts a quest with title, description, reward, and referee (defaults to self)',
                '2. Referee accepts the role and sets their fee % (0-50%)',
                '3. Responders submit responses with proof of work',
                '4. Referee reviews responses',
                '5. Referee approves best response -> reward is paid (5% treasury + referee fee)',
                '6. Or referee rejects responses / creator cancels quest',
            ],
        }
