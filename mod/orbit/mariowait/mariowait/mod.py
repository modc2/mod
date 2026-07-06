import sys, os, time, random, tty, termios, select, threading

class MarioWait:
    """ASCII Mario runner game — play while you wait!"""

    name = 'mariowait'

    def __init__(self, mod=None):
        self.mod = mod

    def play(self):
        """Launch the ASCII Mario game"""
        game = Game()
        game.run()

    def __call__(self):
        self.play()


class Game:
    WIDTH = 60
    HEIGHT = 18
    GROUND_Y = 14
    GRAVITY = 0.6
    JUMP_VEL = -2.2
    SCROLL_SPEED = 1

    MARIO_IDLE = [
        "  ███  ",
        " █████ ",
        " ▐█▀█▌ ",
        "  ███  ",
        " █████ ",
        " █ █ █ ",
        "  █ █  ",
    ]

    MARIO_RUN1 = [
        "  ███  ",
        " █████ ",
        " ▐█▀█▌ ",
        "  ███  ",
        " █████ ",
        "  █ █  ",
        " █   █ ",
    ]

    MARIO_RUN2 = [
        "  ███  ",
        " █████ ",
        " ▐█▀█▌ ",
        "  ███  ",
        " █████ ",
        " █   █ ",
        "  █ █  ",
    ]

    MARIO_JUMP = [
        "  ███  ",
        " █████ ",
        " ▐█▀█▌ ",
        "  ███  ",
        "██████▌",
        "  █ █  ",
        "  █ █  ",
    ]

    GOOMBA = [
        " ▄██▄ ",
        "██▀▀██",
        " █  █ ",
    ]

    PIPE_TOP = [
        "┌████████┐",
        "│████████│",
    ]
    PIPE_BODY = "│████████│"

    COIN = [
        " ◉ ",
    ]

    CLOUD1 = [
        "   ___   ",
        " (     ) ",
        "(       )",
        " ~~~~~~~ ",
    ]

    MUSHROOM = [
        " ▄██▄ ",
        "██▀▀██",
        " │  │ ",
    ]

    def __init__(self):
        self.mario_x = 8
        self.mario_y = float(self.GROUND_Y - len(self.MARIO_IDLE))
        self.vel_y = 0
        self.on_ground = True
        self.is_jumping = False
        self.frame = 0
        self.score = 0
        self.coins = 0
        self.lives = 3
        self.scroll_offset = 0.0
        self.obstacles = []
        self.coin_objects = []
        self.clouds = []
        self.particles = []
        self.game_over = False
        self.paused = False
        self.speed_mult = 1.0
        self.distance = 0
        self.high_score = 0
        self.combo = 0
        self.invincible = 0
        self.mushrooms = []
        self.big = False
        self.big_timer = 0
        self._spawn_initial()

    def _spawn_initial(self):
        for i in range(3):
            self.clouds.append({
                'x': random.randint(0, self.WIDTH),
                'y': random.randint(0, 5),
                'speed': random.uniform(0.2, 0.5)
            })
        self._schedule_obstacle(self.WIDTH + 10)

    def _schedule_obstacle(self, min_x):
        x = min_x + random.randint(8, 20)
        kind = random.choices(['goomba', 'pipe', 'double_goomba'], weights=[50, 35, 15])[0]
        if kind == 'pipe':
            h = random.randint(2, 4)
            self.obstacles.append({'kind': 'pipe', 'x': x, 'h': h, 'alive': True})
            # sometimes put a coin above pipe
            if random.random() < 0.5:
                self.coin_objects.append({'x': x + 3, 'y': self.GROUND_Y - h - 3, 'alive': True})
        elif kind == 'goomba':
            self.obstacles.append({'kind': 'goomba', 'x': x, 'alive': True})
        elif kind == 'double_goomba':
            self.obstacles.append({'kind': 'goomba', 'x': x, 'alive': True})
            self.obstacles.append({'kind': 'goomba', 'x': x + 4, 'alive': True})

        # random coins
        if random.random() < 0.4:
            cx = x + random.randint(-5, 15)
            cy = self.GROUND_Y - random.randint(4, 8)
            for i in range(random.randint(1, 3)):
                self.coin_objects.append({'x': cx + i * 3, 'y': cy, 'alive': True})

        # random mushroom
        if random.random() < 0.15:
            mx = x + random.randint(5, 15)
            self.mushrooms.append({'x': mx, 'y': self.GROUND_Y - 3, 'alive': True})

    def _get_key(self):
        if select.select([sys.stdin], [], [], 0)[0]:
            ch = sys.stdin.read(1)
            if ch == '\x1b':
                if select.select([sys.stdin], [], [], 0)[0]:
                    ch2 = sys.stdin.read(1)
                    if ch2 == '[':
                        if select.select([sys.stdin], [], [], 0)[0]:
                            ch3 = sys.stdin.read(1)
                            return f'arrow_{ch3}'
                return 'esc'
            return ch
        return None

    def _update(self):
        if self.game_over or self.paused:
            return

        self.frame += 1
        self.speed_mult = 1.0 + self.distance / 500.0
        spd = self.SCROLL_SPEED * self.speed_mult

        # gravity
        if not self.on_ground:
            self.vel_y += self.GRAVITY
            self.mario_y += self.vel_y
            ground = self.GROUND_Y - len(self.MARIO_IDLE)
            if self.mario_y >= ground:
                self.mario_y = ground
                self.vel_y = 0
                self.on_ground = True
                self.is_jumping = False

        # scroll objects
        self.scroll_offset += spd
        self.distance += spd

        for ob in self.obstacles:
            ob['x'] -= spd
        for c in self.coin_objects:
            c['x'] -= spd
        for m in self.mushrooms:
            m['x'] -= spd
        for cl in self.clouds:
            cl['x'] -= cl['speed']
            if cl['x'] < -12:
                cl['x'] = self.WIDTH + random.randint(0, 10)
                cl['y'] = random.randint(0, 5)

        # particles
        for p in self.particles:
            p['x'] += p['vx']
            p['y'] += p['vy']
            p['life'] -= 1
        self.particles = [p for p in self.particles if p['life'] > 0]

        # spawn new obstacles
        max_x = max((ob['x'] for ob in self.obstacles), default=0)
        if max_x < self.WIDTH + 5:
            self._schedule_obstacle(self.WIDTH + 5)

        # cleanup offscreen
        self.obstacles = [ob for ob in self.obstacles if ob['x'] > -15 and ob['alive']]
        self.coin_objects = [c for c in self.coin_objects if c['x'] > -5 and c['alive']]
        self.mushrooms = [m for m in self.mushrooms if m['x'] > -8 and m['alive']]

        # invincibility timer
        if self.invincible > 0:
            self.invincible -= 1
        if self.big_timer > 0:
            self.big_timer -= 1
            if self.big_timer == 0:
                self.big = False

        # collisions
        mario_w = 7
        mario_h = len(self.MARIO_IDLE)
        mx1 = self.mario_x
        mx2 = self.mario_x + mario_w
        my1 = int(self.mario_y)
        my2 = int(self.mario_y) + mario_h

        for ob in self.obstacles:
            if not ob['alive']:
                continue
            if ob['kind'] == 'goomba':
                gx1 = int(ob['x'])
                gx2 = gx1 + 6
                gy1 = self.GROUND_Y - 3
                gy2 = self.GROUND_Y
                if mx2 > gx1 + 1 and mx1 < gx2 - 1 and my2 > gy1 and my1 < gy2:
                    # stomp from above
                    if self.vel_y > 0 and my2 - gy1 < 6:
                        ob['alive'] = False
                        self.vel_y = self.JUMP_VEL * 0.6
                        self.on_ground = False
                        self.score += 100 * (1 + self.combo)
                        self.combo += 1
                        for _ in range(5):
                            self.particles.append({
                                'x': ob['x'] + 3, 'y': gy1,
                                'vx': random.uniform(-1, 1),
                                'vy': random.uniform(-2, 0),
                                'char': random.choice(['*', '·', '✦', '+']),
                                'life': random.randint(4, 8)
                            })
                    elif self.invincible == 0:
                        self._hit()
            elif ob['kind'] == 'pipe':
                px1 = int(ob['x'])
                px2 = px1 + 11
                py1 = self.GROUND_Y - ob['h'] - 2
                py2 = self.GROUND_Y
                if mx2 > px1 + 2 and mx1 < px2 - 2 and my2 > py1 + 1 and my1 < py2:
                    if self.invincible == 0:
                        self._hit()

        for c in self.coin_objects:
            if not c['alive']:
                continue
            cx, cy = int(c['x']), int(c['y'])
            if mx2 > cx and mx1 < cx + 3 and my2 > cy and my1 < cy + 1:
                c['alive'] = False
                self.coins += 1
                self.score += 50
                self.particles.append({
                    'x': cx, 'y': cy - 1,
                    'vx': 0, 'vy': -0.5,
                    'char': '+50',
                    'life': 8
                })

        for m in self.mushrooms:
            if not m['alive']:
                continue
            mx_m = int(m['x'])
            my_m = int(m['y'])
            if mx2 > mx_m and mx1 < mx_m + 6 and my2 > my_m and my1 < my_m + 3:
                m['alive'] = False
                self.big = True
                self.big_timer = 200
                self.score += 200
                self.particles.append({
                    'x': mx_m + 3, 'y': my_m - 1,
                    'vx': 0, 'vy': -0.5,
                    'char': 'POWER UP!',
                    'life': 12
                })

    def _hit(self):
        if self.big:
            self.big = False
            self.big_timer = 0
            self.invincible = 30
            return
        self.lives -= 1
        self.combo = 0
        self.invincible = 30
        if self.lives <= 0:
            self.game_over = True
            if self.score > self.high_score:
                self.high_score = self.score

    def _render(self):
        buf = []
        # build screen buffer
        screen = [[' '] * self.WIDTH for _ in range(self.HEIGHT)]

        # sky gradient (subtle)
        for y in range(self.GROUND_Y):
            for x in range(self.WIDTH):
                screen[y][x] = ' '

        # clouds
        for cl in self.clouds:
            for dy, line in enumerate(self.CLOUD1):
                y = int(cl['y']) + dy
                for dx, ch in enumerate(line):
                    x = int(cl['x']) + dx
                    if 0 <= x < self.WIDTH and 0 <= y < self.HEIGHT and ch != ' ':
                        screen[y][x] = ch

        # ground
        for x in range(self.WIDTH):
            if self.GROUND_Y < self.HEIGHT:
                screen[self.GROUND_Y][x] = '▓'
            for gy in range(self.GROUND_Y + 1, self.HEIGHT):
                screen[gy][x] = '░'

        # pipes
        for ob in self.obstacles:
            if not ob['alive']:
                continue
            if ob['kind'] == 'pipe':
                px = int(ob['x'])
                for dy, line in enumerate(self.PIPE_TOP):
                    y = self.GROUND_Y - ob['h'] - 2 + dy
                    for dx, ch in enumerate(line):
                        x = px + dx
                        if 0 <= x < self.WIDTH and 0 <= y < self.HEIGHT:
                            screen[y][x] = ch
                for body_y in range(self.GROUND_Y - ob['h'], self.GROUND_Y):
                    for dx, ch in enumerate(self.PIPE_BODY):
                        x = px + dx
                        if 0 <= x < self.WIDTH and 0 <= y < self.HEIGHT:
                            screen[body_y][x] = ch

        # goombas
        for ob in self.obstacles:
            if not ob['alive'] or ob['kind'] != 'goomba':
                continue
            gx = int(ob['x'])
            gy = self.GROUND_Y - 3
            sprite = self.GOOMBA
            for dy, line in enumerate(sprite):
                for dx, ch in enumerate(line):
                    x = gx + dx
                    y = gy + dy
                    if 0 <= x < self.WIDTH and 0 <= y < self.HEIGHT and ch != ' ':
                        screen[y][x] = ch

        # coins
        for c in self.coin_objects:
            if not c['alive']:
                continue
            cx, cy = int(c['x']), int(c['y'])
            blink = (self.frame // 4) % 2
            char = '◉' if blink else '○'
            if 0 <= cx < self.WIDTH - 2 and 0 <= cy < self.HEIGHT:
                screen[cy][cx + 1] = char

        # mushrooms
        for m in self.mushrooms:
            if not m['alive']:
                continue
            mx_m, my_m = int(m['x']), int(m['y'])
            for dy, line in enumerate(self.MUSHROOM):
                for dx, ch in enumerate(line):
                    x = mx_m + dx
                    y = my_m + dy
                    if 0 <= x < self.WIDTH and 0 <= y < self.HEIGHT and ch != ' ':
                        screen[y][x] = ch

        # mario
        if self.invincible == 0 or self.frame % 2 == 0:
            if self.is_jumping:
                sprite = self.MARIO_JUMP
            elif not self.on_ground:
                sprite = self.MARIO_JUMP
            elif self.frame % 8 < 4:
                sprite = self.MARIO_RUN1
            else:
                sprite = self.MARIO_RUN2

            my = int(self.mario_y)
            for dy, line in enumerate(sprite):
                for dx, ch in enumerate(line):
                    x = self.mario_x + dx
                    y = my + dy
                    if 0 <= x < self.WIDTH and 0 <= y < self.HEIGHT and ch != ' ':
                        screen[y][x] = ch

        # particles
        for p in self.particles:
            px, py = int(p['x']), int(p['y'])
            for i, ch in enumerate(p['char']):
                x = px + i
                if 0 <= x < self.WIDTH and 0 <= py < self.HEIGHT:
                    screen[py][x] = ch

        # HUD
        hud = f" ♥ {self.lives}  ★ {self.score:06d}  ◉ {self.coins:02d}  ≡ {int(self.distance)}m "
        if self.big:
            hud += " [POWER] "
        hud = hud.center(self.WIDTH)

        # compose
        border = '═' * self.WIDTH
        buf.append(f"╔{border}╗")
        buf.append(f"║\033[33m{hud}\033[0m║")
        buf.append(f"╠{border}╣")
        for row in screen:
            line = ''.join(row)
            buf.append(f"║{line}║")
        buf.append(f"╚{border}╝")

        if self.game_over:
            # overlay game over
            mid = len(buf) // 2
            go_text = "  ☠  G A M E   O V E R  ☠  "
            sc_text = f"  Score: {self.score}  Best: {self.high_score}  "
            rt_text = "  [R] Retry  [Q] Quit  "
            for i, txt in enumerate([go_text, sc_text, "", rt_text]):
                padded = txt.center(self.WIDTH)
                buf[mid - 1 + i] = f"║\033[1;31m{padded}\033[0m║"

        if self.paused:
            mid = len(buf) // 2
            pt = "  ⏸  P A U S E D  ⏸  ".center(self.WIDTH)
            buf[mid] = f"║\033[1;33m{pt}\033[0m║"

        return '\n'.join(buf)

    def run(self):
        old_settings = termios.tcgetattr(sys.stdin)
        try:
            tty.setcbreak(sys.stdin.fileno())
            sys.stdout.write('\033[2J\033[?25l')  # clear + hide cursor
            sys.stdout.flush()

            print("\033[2J\033[H")
            print("""
╔══════════════════════════════════════════════════════════╗
║                                                          ║
║              🍄  M A R I O W A I T  🍄                  ║
║                                                          ║
║           An ASCII adventure while you wait!             ║
║                                                          ║
║    Controls:                                             ║
║      SPACE / W / ↑  ─  Jump                              ║
║      P             ─  Pause                              ║
║      Q / ESC       ─  Quit                               ║
║                                                          ║
║    ★ Stomp goombas for points!                           ║
║    ◉ Collect coins!                                      ║
║    🍄 Grab mushrooms for power!                          ║
║                                                          ║
║           Press any key to start...                      ║
║                                                          ║
╚══════════════════════════════════════════════════════════╝
            """)

            # wait for key
            while True:
                k = self._get_key()
                if k:
                    break
                time.sleep(0.05)

            target_fps = 20
            frame_time = 1.0 / target_fps

            while True:
                t0 = time.time()

                # input
                key = self._get_key()
                if key:
                    if key in ('q', 'esc'):
                        break
                    elif key == 'p':
                        self.paused = not self.paused
                    elif key == 'r' and self.game_over:
                        self.__init__()
                    elif key in (' ', 'w', 'arrow_A') and not self.game_over and not self.paused:
                        if self.on_ground:
                            self.vel_y = self.JUMP_VEL
                            self.on_ground = False
                            self.is_jumping = True
                            self.combo = 0

                self._update()

                # render
                frame = self._render()
                sys.stdout.write(f'\033[H{frame}')
                sys.stdout.flush()

                elapsed = time.time() - t0
                sleep_time = frame_time - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)

        finally:
            sys.stdout.write('\033[?25h')  # show cursor
            sys.stdout.flush()
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
            print(f"\n  Thanks for playing! Final score: {self.score}\n")


if __name__ == '__main__':
    game = Game()
    game.run()
