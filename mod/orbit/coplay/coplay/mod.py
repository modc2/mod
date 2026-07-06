import mod as m
import os
import json
import time
from typing import Dict, Any, List, Optional


class Mod:
    """
    CoPlay - Organize pickup games, charge entry fees in crypto.

    On-chain: CoPlayHub smart contract handles entry fees, payouts, admin fee (0-10%).
    Off-chain: This module stores game metadata (title, description, location, etc.)
    and maps on-chain game IDs to rich metadata.

    Flow:
    1. Organizer creates a game (on-chain + off-chain metadata)
    2. Players join by paying entry fee through the contract
    3. Organizer completes the game -> funds released minus admin fee
    4. Admin can require approval, set fee %, remove games
    """

    description = """
    CoPlay - Organize pickup games at any location. Charge crypto entry fees
    via smart contract with admin fee (0-10%). Supports MetaMask and local wallets.
    """

    folder_path = m.abspath('~/.mod/coplay')

    def __init__(self, auth='auth.base'):
        self.auth = m.mod(auth)()
        self.games_path = os.path.join(self.folder_path, 'games')
        self.config_path = os.path.join(self.folder_path, 'config.json')
        os.makedirs(self.games_path, exist_ok=True)
        self._ensure_config()

    def _ensure_config(self):
        if not os.path.exists(self.config_path):
            config = {
                'admins': [],
                'contract_address': None,
                'chain_id': 84532,
            }
            m.put(self.config_path, config)

    def _get_config(self):
        return m.get(self.config_path)

    def _is_admin(self, key: str) -> bool:
        config = self._get_config()
        return key in config.get('admins', [])

    # ==================== PUBLIC ====================

    def forward(self, status: str = None, game_type: str = None,
                n: int = 50, page: int = 0) -> List[Dict[str, Any]]:
        """List all games, optionally filtered."""
        return self.games(status=status, game_type=game_type, n=n, page=page)

    def games(self, status: str = None, game_type: str = None,
              n: int = 50, page: int = 0) -> List[Dict[str, Any]]:
        """List all games, optionally filtered by status or game type."""
        import glob
        game_files = glob.glob(os.path.join(self.games_path, '*.json'))

        all_games = []
        for gf in game_files:
            try:
                game = m.get(gf)
                if game:
                    all_games.append(game)
            except:
                continue

        if status:
            all_games = [g for g in all_games if g.get('status') == status]
        if game_type:
            gt = game_type.lower()
            all_games = [g for g in all_games if gt in g.get('game_type', '').lower()]

        all_games.sort(key=lambda x: x.get('created_at', 0), reverse=True)

        start = page * n
        return all_games[start:start + n]

    def get_game(self, game_id: str) -> Dict[str, Any]:
        """Get a specific game by ID."""
        game_path = os.path.join(self.games_path, f"{game_id}.json")
        game = m.get(game_path, None)
        assert game is not None, f"Game {game_id} not found"
        return game

    def get_config(self) -> Dict[str, Any]:
        """Get public config (contract address, chain_id)."""
        config = self._get_config()
        return {
            'contract_address': config.get('contract_address'),
            'chain_id': config.get('chain_id', 84532),
        }

    # ==================== AUTH REQUIRED ====================

    def create_game(self, token: str, title: str, description: str = '',
                    game_type: str = '', location: str = '',
                    date: str = '', time_str: str = '',
                    max_players: int = 0, entry_fee: str = '0',
                    chain_game_id: int = None) -> Dict[str, Any]:
        """
        Create a game. Stores off-chain metadata linked to on-chain game ID.

        Args:
            token: Auth token
            title: Game title
            description: Game description
            game_type: Free text game type (e.g. "basketball", "poker")
            location: Where the game is held
            date: Date string (e.g. "2026-04-20")
            time_str: Time string (e.g. "18:00")
            max_players: Max players (0 = unlimited)
            entry_fee: Entry fee in ETH as string (e.g. "0.005")
            chain_game_id: The on-chain game ID from the contract

        Returns:
            Game metadata object
        """
        assert token is not None, "Auth token required"
        verified = self.auth.verify(token)
        creator_key = verified['key']

        assert title and len(title.strip()) > 0, "Title is required"

        game_id = m.hash(f"{creator_key}:{title}:{time.time()}")[:16]

        game = {
            'id': game_id,
            'chain_game_id': chain_game_id,
            'organizer': creator_key,
            'title': title.strip(),
            'description': (description or '').strip(),
            'game_type': (game_type or '').strip(),
            'location': (location or '').strip(),
            'date': (date or '').strip(),
            'time': (time_str or '').strip(),
            'max_players': max_players,
            'entry_fee': entry_fee,
            'players': [],
            'status': 'open',
            'created_at': time.time(),
            'updated_at': time.time(),
        }

        game_path = os.path.join(self.games_path, f"{game_id}.json")
        m.put(game_path, game)
        return game

    def join_game(self, token: str, game_id: str,
                  tx_hash: str = None) -> Dict[str, Any]:
        """
        Record a player joining a game (after on-chain payment).

        Args:
            token: Auth token
            game_id: Off-chain game ID
            tx_hash: On-chain transaction hash as payment proof
        """
        assert token is not None, "Auth token required"
        verified = self.auth.verify(token)
        player_key = verified['key']

        game = self.get_game(game_id)
        assert game['status'] in ('open', 'full'), "Game is not open"
        assert player_key not in game.get('players', []), "Already joined"

        game['players'].append(player_key)
        if not game.get('tx_hashes'):
            game['tx_hashes'] = {}
        if tx_hash:
            game['tx_hashes'][player_key] = tx_hash

        if game['max_players'] > 0 and len(game['players']) >= game['max_players']:
            game['status'] = 'full'

        game['updated_at'] = time.time()

        game_path = os.path.join(self.games_path, f"{game_id}.json")
        m.put(game_path, game)
        return game

    def leave_game(self, token: str, game_id: str) -> Dict[str, Any]:
        """Remove yourself from a game."""
        assert token is not None, "Auth token required"
        verified = self.auth.verify(token)
        player_key = verified['key']

        game = self.get_game(game_id)
        assert player_key in game.get('players', []), "Not in this game"
        assert game['status'] in ('open', 'full'), "Game is not active"

        game['players'].remove(player_key)
        if game.get('tx_hashes'):
            game['tx_hashes'].pop(player_key, None)

        if game['status'] == 'full' and game['max_players'] > 0:
            if len(game['players']) < game['max_players']:
                game['status'] = 'open'

        game['updated_at'] = time.time()
        game_path = os.path.join(self.games_path, f"{game_id}.json")
        m.put(game_path, game)
        return game

    def my_games(self, token: str) -> Dict[str, List[Dict[str, Any]]]:
        """Get games I organized and games I joined."""
        assert token is not None, "Auth token required"
        verified = self.auth.verify(token)
        user_key = verified['key']

        all_games = self.games(n=10000)

        organized = [g for g in all_games if g.get('organizer') == user_key]
        joined = [g for g in all_games if user_key in g.get('players', [])]

        return {'organized': organized, 'joined': joined}

    def cancel_game(self, token: str, game_id: str) -> Dict[str, Any]:
        """Cancel a game. Only the organizer or admin can cancel."""
        assert token is not None, "Auth token required"
        verified = self.auth.verify(token)
        caller_key = verified['key']

        game = self.get_game(game_id)
        is_organizer = game['organizer'] == caller_key
        is_admin = self._is_admin(caller_key)
        assert is_organizer or is_admin, "Only organizer or admin can cancel"
        assert game['status'] in ('open', 'full', 'pending_approval'), "Cannot cancel"

        game['status'] = 'cancelled'
        game['cancelled_at'] = time.time()
        game['updated_at'] = time.time()

        game_path = os.path.join(self.games_path, f"{game_id}.json")
        m.put(game_path, game)
        return game

    def complete_game(self, token: str, game_id: str,
                      tx_hash: str = None) -> Dict[str, Any]:
        """Mark a game as completed (after on-chain settlement)."""
        assert token is not None, "Auth token required"
        verified = self.auth.verify(token)
        caller_key = verified['key']

        game = self.get_game(game_id)
        assert game['organizer'] == caller_key, "Only organizer can complete"
        assert game['status'] in ('open', 'full'), "Cannot complete"

        game['status'] = 'completed'
        game['completed_at'] = time.time()
        if tx_hash:
            game['completion_tx'] = tx_hash
        game['updated_at'] = time.time()

        game_path = os.path.join(self.games_path, f"{game_id}.json")
        m.put(game_path, game)
        return game

    # ==================== ADMIN ====================

    def admin_set_config(self, token: str, contract_address: str = None,
                         chain_id: int = None,
                         add_admin: str = None,
                         remove_admin: str = None) -> Dict[str, Any]:
        """Update coplay config. Admin only."""
        assert token is not None, "Auth token required"
        verified = self.auth.verify(token)
        caller_key = verified['key']
        assert self._is_admin(caller_key), "Admin only"

        config = self._get_config()
        if contract_address is not None:
            config['contract_address'] = contract_address
        if chain_id is not None:
            config['chain_id'] = chain_id
        if add_admin:
            if add_admin not in config['admins']:
                config['admins'].append(add_admin)
        if remove_admin:
            if remove_admin in config['admins']:
                config['admins'].remove(remove_admin)

        m.put(self.config_path, config)
        return config

    def admin_approve_game(self, token: str, game_id: str) -> Dict[str, Any]:
        """Approve a pending game."""
        assert token is not None, "Auth token required"
        verified = self.auth.verify(token)
        assert self._is_admin(verified['key']), "Admin only"

        game = self.get_game(game_id)
        assert game['status'] == 'pending_approval', "Game not pending"
        game['status'] = 'open'
        game['updated_at'] = time.time()

        game_path = os.path.join(self.games_path, f"{game_id}.json")
        m.put(game_path, game)
        return game

    def admin_reject_game(self, token: str, game_id: str,
                          reason: str = '') -> Dict[str, Any]:
        """Reject a pending game."""
        assert token is not None, "Auth token required"
        verified = self.auth.verify(token)
        assert self._is_admin(verified['key']), "Admin only"

        game = self.get_game(game_id)
        assert game['status'] == 'pending_approval', "Game not pending"
        game['status'] = 'cancelled'
        game['rejection_reason'] = reason
        game['updated_at'] = time.time()

        game_path = os.path.join(self.games_path, f"{game_id}.json")
        m.put(game_path, game)
        return game

    def admin_remove_game(self, token: str, game_id: str) -> Dict[str, Any]:
        """Remove any game (admin only)."""
        assert token is not None, "Auth token required"
        verified = self.auth.verify(token)
        assert self._is_admin(verified['key']), "Admin only"

        game = self.get_game(game_id)
        game['status'] = 'removed'
        game['updated_at'] = time.time()

        game_path = os.path.join(self.games_path, f"{game_id}.json")
        m.put(game_path, game)
        return game

    def admin_get_pending(self, token: str) -> List[Dict[str, Any]]:
        """List games pending approval."""
        assert token is not None, "Auth token required"
        verified = self.auth.verify(token)
        assert self._is_admin(verified['key']), "Admin only"
        return self.games(status='pending_approval', n=10000)

    def stats(self) -> Dict[str, Any]:
        """Get overall stats."""
        all_games = self.games(n=10000)
        return {
            'total_games': len(all_games),
            'open': len([g for g in all_games if g['status'] in ('open', 'full')]),
            'completed': len([g for g in all_games if g['status'] == 'completed']),
            'cancelled': len([g for g in all_games if g['status'] == 'cancelled']),
            'total_players': sum(len(g.get('players', [])) for g in all_games),
            'game_types': list(set(
                g.get('game_type', '').strip().lower()
                for g in all_games if g.get('game_type', '').strip()
            )),
        }

    def info(self) -> Dict[str, Any]:
        """Module info."""
        return {
            'name': 'coplay',
            'description': 'Organize pickup games with crypto entry fees via smart contract',
            'version': '1.0.0',
            'stats': self.stats(),
        }
