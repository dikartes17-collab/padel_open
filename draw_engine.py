"""Жеребьёвка турнира «каждый с каждым в паре» (Whist-турнир).

Формат (для n = 4, 8, 12 игроков):
- каждая пара игроков играет в одной паре ровно 1 раз (всего n*(n-1)/4 игры);
- каждая пара игроков встречается соперниками ровно 2 раза (это математически
  неизбежно при полном формате «каждый с каждым в паре»);
- n-1 туров по n/4 игры, все игроки играют каждый тур.

Сетка строется на основе Z-cyclic Whist-турниров. Для 12 игроков известно ровно
два неизоморфных решения (Haanpää, Östergård, 2003); они зашиты здесь как начальные
туры, остальные туры получаются сдвигом (mod 11).

Дополнительные оптимизации:
1. Перестановка игроков по «слотам» сетки — минимизируем суммарный дисбаланс
   рейтингов в каждой игре: |сумма пары A - сумма пары B|. Это и есть правило
   «не ставить двух слабых против двух сильных» — слабые всегда миксуются
   с сильными.
2. Порядок туров — встречи одной пары соперников разносятся по турам
   (по возможности не подряд).
"""

from __future__ import annotations

import random
import time

# Начальные туры. Элемент -1 означает игрока «бесконечность» (∞),
# конечные игроки нумеруются 0..n-2, тур r = начальный тур + r (mod n-1).
DESIGNS: dict[int, list[tuple[tuple[int, ...], ...]]] = {
    4: [((-1, 0, 1, 2),)],
    8: [
        ((-1, 1, 0, 3), (2, 4, 6, 5)),
    ],
    12: [
        ((-1, 1, 0, 4), (2, 3, 6, 8), (5, 9, 7, 10)),
        ((-1, 1, 0, 7), (2, 3, 5, 10), (4, 8, 6, 9)),
    ],
}

# штрафы за малый промежуток между двумя встречами одной пары соперников
_GAP_PENALTY = (0, 100, 12, 4, 1, 0, 0, 0, 0, 0, 0)


def supported_counts() -> list[int]:
    return sorted(DESIGNS)


def generate_rounds(n: int, design_index: int) -> list[list[tuple[tuple[int, int], tuple[int, int]]]]:
    """Туры как списки игр; игра = ((пара A), (пара B)), пары = партнёры."""
    m = n - 1
    inf = n - 1
    design = DESIGNS[n][design_index]
    rounds = []
    for r in range(m):
        games = []
        for tup in design:
            g = tuple(inf if x == -1 else (x + r) % m for x in tup)
            games.append(((g[0], g[2]), (g[1], g[3])))
        rounds.append(games)
    return rounds


def verify(n: int, rounds: list) -> None:
    """Каждая пара - партнёры ровно 1 раз и соперники ровно 2 раза."""
    partners: dict[frozenset, int] = {}
    opponents: dict[frozenset, int] = {}
    games_total = 0
    for games in rounds:
        for (a, b), (c, d) in games:
            games_total += 1
            for p in ((a, b), (c, d)):
                partners[frozenset(p)] = partners.get(frozenset(p), 0) + 1
            for p in ((a, c), (a, d), (b, c), (b, d)):
                opponents[frozenset(p)] = opponents.get(frozenset(p), 0) + 1
    expected_games = n * (n - 1) // 4
    if games_total != expected_games:
        raise AssertionError(f"игр {games_total}, ожидалось {expected_games}")
    for p, cnt in partners.items():
        if cnt != 1:
            raise AssertionError(f"пара {sorted(p)} была партнёрами {cnt} раз")
    for p, cnt in opponents.items():
        if cnt != 2:
            raise AssertionError(f"пара {sorted(p)} была соперниками {cnt} раза")


def opponent_meetings(n: int, rounds: list) -> dict[frozenset, tuple[int, int]]:
    """Туры, в которых пара встречалась как соперники (ровно 2 тура)."""
    meetings: dict[frozenset, list[int]] = {}
    for ri, games in enumerate(rounds):
        for (a, b), (c, d) in games:
            for p in ((a, c), (a, d), (b, c), (b, d)):
                meetings.setdefault(frozenset(p), []).append(ri)
    return {p: (v[0], v[1]) for p, v in meetings.items()}


def optimize_round_order(n: int, rounds: list) -> list[int]:
    """Порядок туров: две встречи одной пары соперников - как можно дальше друг от друга."""
    meetings = opponent_meetings(n, rounds)
    m = len(rounds)
    rng = random.Random(11)
    order = list(range(m))

    def cost(o: list[int]) -> int:
        total = 0
        for (r1, r2) in meetings.values():
            gap = abs(o[r1] - o[r2])
            total += _GAP_PENALTY[gap]
        return total

    best_c = cost(order)
    best_o = list(order)
    for _ in range(20000):
        i, j = rng.randrange(m), rng.randrange(m)
        if i == j:
            continue
        order[i], order[j] = order[j], order[i]
        c = cost(order)
        if c <= best_c:
            best_c, best_o = c, list(order)
        else:
            order[i], order[j] = order[j], order[i]
        if best_c == 0:
            break
    return best_o


# вес максимального дисбаланса в стоимости перестановки игроков:
# сильнее штрафуем «два слабых против двух сильных», даже если суммарный
# дисбаланс остальных игр чуть выше
MAX_WEIGHT = 10.0


def optimize_players(n: int, rounds: list, ratings: list[float], seed=None,
                     restarts=8, moves=30000) -> tuple[list[int], float, float]:
    """Перестановка игроков по слотам сетки.

    Возвращает (perm, max_imbalance, sum_imbalance), где perm[слот] = игрок.
    Стоимость = MAX_WEIGHT * max_дисбаланс + сумма_дисбалансов.
    """
    rng = random.Random(seed)
    flat = [g for games in rounds for g in games]
    ngames = len(flat)
    by_slot: list[list[int]] = [[] for _ in range(n)]
    for gi, ((a, b), (c, d)) in enumerate(flat):
        for s in (a, b, c, d):
            by_slot[s].append(gi)

    def game_val(gi, perm):
        (a, b), (c, d) = flat[gi]
        return abs(ratings[perm[a]] + ratings[perm[b]] - ratings[perm[c]] - ratings[perm[d]])

    best = None
    for _ in range(restarts):
        perm = list(range(n))
        rng.shuffle(perm)
        vals = [game_val(gi, perm) for gi in range(ngames)]
        cur = MAX_WEIGHT * max(vals) + sum(vals)
        stale = 0
        for step in range(moves):
            temperature = 0.6 * (1 - step / moves) + 0.01
            i, j = rng.randrange(n), rng.randrange(n)
            if i == j:
                continue
            affected = sorted(set(by_slot[i]) | set(by_slot[j]))
            perm[i], perm[j] = perm[j], perm[i]
            for gi in affected:
                vals[gi] = game_val(gi, perm)
            new_cost = MAX_WEIGHT * max(vals) + sum(vals)
            d = new_cost - cur
            if d <= 0 or rng.random() < pow(2.718281828, -d / temperature):
                cur = new_cost
                stale = 0
                mx, sm = max(vals), sum(vals)
                if best is None or (mx, sm) < (best[1], best[2]):
                    best = (list(perm), mx, sm)
            else:
                perm[i], perm[j] = perm[j], perm[i]
                for gi in affected:
                    vals[gi] = game_val(gi, perm)
                stale += 1
            if stale > 5000:
                break
    return best


def build_draw(ratings: list[float], seed=None) -> dict:
    """Строит сетку турнира. Возвращает:

    {
      'rounds': [ [ {'a': [id, id], 'b': [id, id]}, ... ], ... ],  # туры по порядку
      'imbalance': суммарный дисбаланс рейтингов,
      'design': индекс варианта сетки,
    }
    id - индексы из списка ratings.
    """
    n = len(ratings)
    if n not in DESIGNS:
        raise ValueError(f"формат рассчитан на {', '.join(map(str, supported_counts()))} участников, а сейчас {n}")

    rng = random.Random(seed)
    best = None
    for di in range(len(DESIGNS[n])):
        rounds = generate_rounds(n, di)
        verify(n, rounds)
        order = optimize_round_order(n, rounds)
        perm, mx, sm = optimize_players(n, rounds, ratings, seed=rng.randrange(10**9))
        # perm: слот -> игрок; применяем к турам, затем переупорядочиваем туры по order
        def apply(games):
            out = []
            for (a, b), (c, d) in games:
                out.append(((perm[a], perm[b]), (perm[c], perm[d])))
            return out
        final_rounds = [apply(rounds[ri]) for ri in order]
        verify(n, final_rounds)
        if best is None or (mx, sm) < (best[0], best[1]):
            best = (mx, sm, di, final_rounds)

    mx, sm, di, final_rounds = best
    return {
        "rounds": [
            [{"a": list(g[0]), "b": list(g[1])} for g in games]
            for games in final_rounds
        ],
        "imbalance": sm,
        "max_imbalance": mx,
        "design": di,
    }


if __name__ == "__main__":
    rng = random.Random(1)
    ratings = [round(rng.uniform(2.0, 6.5), 1) for _ in range(12)]
    t0 = time.time()
    draw = build_draw(ratings, seed=1)
    dt = time.time() - t0
    print(f"время: {dt:.2f}с, вариант сетки: {draw['design']}, "
          f"суммарный дисбаланс: {draw['imbalance']:.1f}, максимум в игре: {draw['max_imbalance']:.1f}")

    n = 12
    for ri, games in enumerate(draw["rounds"], 1):
        print(f"Тур {ri}:")
        for g in games:
            sa = sum(ratings[i] for i in g["a"])
            sb = sum(ratings[i] for i in g["b"])
            print(f"  {g['a']} ({sa:.1f}) vs {g['b']} ({sb:.1f})  |diff|={abs(sa-sb):.1f}")

    # статистика разноса встреч
    flat_rounds = []
    for games in draw["rounds"]:
        flat_rounds.append([ (tuple(g["a"]), tuple(g["b"])) for g in games ])
    meetings = opponent_meetings(n, flat_rounds)
    gaps = sorted(abs(r1 - r2) for (r1, r2) in meetings.values())
    from collections import Counter
    print("промежутки между двумя встречами пары:", dict(sorted(Counter(gaps).items())))
