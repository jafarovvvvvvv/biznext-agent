"""Generate the Demo Foods synthetic dataset and load it into schema ``demo``.

Usage:
    python generate.py              # load into SEED_DATABASE_URL (skips if data exists)
    python generate.py --dry-run    # simulate only, print monthly totals (no database needed)

The simulation is deterministic for a given SEED_RANDOM_SEED and end date. All dates are relative
to the end date, so "last month" / "this quarter" always exist in the data. Storylines are
described in docs/ARCHITECTURE.md §3.2.
"""

from __future__ import annotations

import argparse
import calendar
import heapq
import math
import os
import random
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import reference as ref

TABLES_IN_LOAD_ORDER = [
    "regions", "warehouses", "product_categories", "products", "raw_materials", "suppliers",
    "bill_of_materials", "production_lines", "employees", "customers", "purchase_orders",
    "purchase_order_items", "production_batches", "line_downtime", "sales_orders", "sales_order_items",
    "sales_returns", "inventory_snapshots", "write_offs", "operating_expenses", "sales_plans",
]

WEEKDAY_ORDER_FACTOR = [1.15, 1.10, 1.10, 1.10, 1.15, 0.90, 0.50]
RAW_MILK_SEASON = [1.03, 1.035, 1.02, 1.00, 0.98, 0.97, 0.97, 0.98, 0.99, 1.00, 1.02, 1.03]
COLD_CHAIN_CATEGORIES = {2, ref.YOGURT_CATEGORY}  # кисломолочные и йогурты
NET_PRICE_FACTOR = 0.93  # average price after customer discounts
ELECTRICITY_SEASON = [1.18, 1.15, 1.05, 0.97, 0.93, 0.95, 1.00, 1.00, 0.96, 1.02, 1.10, 1.16]
CATEGORY_PREFS: dict[str, dict[int, float]] = {
    "Розничная сеть": {1: 1.0, 2: 1.0, 3: 1.1, 4: 1.0, 5: 1.0, 6: 1.0, 7: 1.0},
    "Дистрибьютор": {1: 1.2, 2: 1.1, 3: 0.9, 4: 0.8, 5: 0.9, 6: 1.0, 7: 1.2},
    "HoReCa": {1: 1.3, 2: 0.9, 3: 0.5, 4: 1.6, 5: 1.8, 6: 0.9, 7: 0.8},
    "Независимый магазин": {1: 1.2, 2: 1.2, 3: 1.0, 4: 0.7, 5: 0.9, 6: 1.1, 7: 1.0},
    "key_cheese_chain": {1: 0.35, 2: 0.35, 3: 0.4, 4: 4.5, 5: 0.6, 6: 0.35, 7: 0.35},
}


# ----------------------------------------------------------------------------- calendar


def month_start(d: date) -> date:
    return d.replace(day=1)


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.year * 12 + d.month - 1 + months, 12)
    return date(y, m + 1, 1)


@dataclass
class Calendar:
    start: date
    end: date
    days: list[date]
    last_full_month: date  # M
    month_before: date  # M-1
    two_months_before: date  # M-2

    @classmethod
    def build(cls, end: date) -> "Calendar":
        start = add_months(month_start(end), -13)
        days = [start + timedelta(n) for n in range((end - start).days + 1)]
        m = add_months(month_start(end), -1)
        return cls(start, end, days, m, add_months(m, -1), add_months(m, -2))

    def month_index(self, d: date) -> int:
        return (d.year - self.start.year) * 12 + d.month - self.start.month


@dataclass
class Stories:
    milk_ramp_start: date
    milk_ramp_end: date
    milk_price_jump: float
    breakdown_days: set[date]
    breakdown_start: datetime
    breakdown_end: datetime
    cold_chain_days: set[date]
    chain_stop: date
    defect_batch_day: date
    return_spike_days: tuple[date, date]
    butter_shortage_start: date
    butter_no_milk_events: list[tuple[datetime, int]]
    overdue_from: date
    price_uplift_day: date | None

    @classmethod
    def build(cls, cal: Calendar, tz: ZoneInfo) -> "Stories":
        m, m1, m2 = cal.last_full_month, cal.month_before, cal.two_months_before
        bd_first = m + timedelta(10)
        bd_last = m + timedelta(14)
        uplift = next((d for d in cal.days if d.month == 1 and d.day == 1 and d > cal.start), None)
        return cls(
            milk_ramp_start=m1 + timedelta(13),
            milk_ramp_end=m1 + timedelta(27),
            milk_price_jump=0.11,
            breakdown_days={bd_first + timedelta(n) for n in range(5)},
            breakdown_start=datetime.combine(bd_first, datetime.min.time(), tz).replace(hour=7, minute=40),
            breakdown_end=datetime.combine(bd_last, datetime.min.time(), tz).replace(hour=19, minute=55),
            cold_chain_days={m + timedelta(5), m + timedelta(6), m + timedelta(19)},
            chain_stop=cal.end - timedelta(80),
            defect_batch_day=m2 + timedelta(8),
            return_spike_days=(m2 + timedelta(11), m2 + timedelta(12)),
            butter_shortage_start=cal.end - timedelta(12),
            butter_no_milk_events=[
                (datetime.combine(cal.end - timedelta(11), datetime.min.time(), tz).replace(hour=6), 610),
                (datetime.combine(cal.end - timedelta(6), datetime.min.time(), tz).replace(hour=5, minute=30), 840),
            ],
            overdue_from=cal.end - timedelta(75),
            price_uplift_day=uplift,
        )


# ----------------------------------------------------------------------------- dataset


@dataclass
class Dataset:
    tables: dict[str, tuple[list[str], list[tuple]]] = field(default_factory=dict)

    def add(self, table: str, columns: list[str], rows: list[tuple]) -> None:
        self.tables[table] = (columns, rows)


@dataclass
class Customer:
    customer_id: int
    name: str
    ctype: str
    region_id: int
    manager_id: int
    terms: int
    size: float
    discount: float
    created_at: date
    pref_key: str
    is_key_chain: bool = False
    late_payer: bool = False


@dataclass
class OrderItem:
    order_ref: int
    product_id: int
    qty: int
    base_qty: int
    unit_price: float
    discount: float
    unit_cost: float


@dataclass
class Order:
    ref: int
    customer: Customer
    warehouse_id: int
    order_date: date
    cancelled: bool
    items: list[int]  # indexes into items list


class Generator:
    def __init__(self, end: date, seed: int, tz: ZoneInfo) -> None:
        self.rng = random.Random(seed)
        self.tz = tz
        self.cal = Calendar.build(end)
        self.st = Stories.build(self.cal, tz)
        self.products = {p.product_id: p for p in ref.PRODUCTS}
        self.materials = {m[0]: m for m in ref.MATERIALS}
        self.category_margin = {c[0]: c[3] for c in ref.CATEGORIES}
        self.n_days = len(self.cal.days)
        self.day_index = {d: i for i, d in enumerate(self.cal.days)}
        self.ds = Dataset()

    # ------------------------------------------------------------------ prices and costs

    def raw_milk_price(self, d: date) -> float:
        base = 205.0 * RAW_MILK_SEASON[d.month - 1] * (1 + 0.003 * self.cal.month_index(d))
        st = self.st
        if d >= st.milk_ramp_end:
            base *= 1 + st.milk_price_jump
        elif d > st.milk_ramp_start:
            share = (d - st.milk_ramp_start).days / (st.milk_ramp_end - st.milk_ramp_start).days
            base *= 1 + st.milk_price_jump * share
        return base

    def material_price(self, material_id: int, d: date) -> float:
        if material_id == ref.RAW_MILK_ID:
            return self.raw_milk_price(d)
        return self.materials[material_id][4] * (1 + 0.003 * self.cal.month_index(d))

    def sale_price(self, product: ref.Product, d: date) -> float:
        uplift = self.st.price_uplift_day
        return product.list_price * (1.05 if uplift and d >= uplift else 1.0)

    def build_unit_costs(self) -> None:
        start = self.cal.start
        self.conversion: dict[int, float] = {}
        for p in ref.PRODUCTS:
            materials = sum(q * self.material_price(m, start) for m, q in p.bom.items())
            conv = p.list_price * NET_PRICE_FACTOR * (1 - self.category_margin[p.category_id]) - materials
            self.conversion[p.product_id] = max(conv, 0.08 * p.list_price)
        self.unit_cost: dict[int, list[float]] = {}
        for p in ref.PRODUCTS:
            costs = []
            for d in self.cal.days:
                mi = self.cal.month_index(d)
                materials = sum(q * self.material_price(m, d) for m, q in p.bom.items())
                costs.append(round(materials + self.conversion[p.product_id] * (1 + 0.0025 * mi), 2))
            self.unit_cost[p.product_id] = costs

    # ------------------------------------------------------------------ reference tables

    def build_reference(self) -> None:
        self.ds.add("regions", ["region_id", "name", "macro_region"], [(r[0], r[1], r[2]) for r in ref.REGIONS])
        self.ds.add("warehouses", ["warehouse_id", "name", "region_id", "warehouse_type", "capacity_pallets"],
                    [tuple(w) for w in ref.WAREHOUSES])
        self.ds.add("product_categories", ["category_id", "name", "description"],
                    [(c[0], c[1], c[2]) for c in ref.CATEGORIES])
        self.ds.add("raw_materials", ["material_id", "name", "unit", "material_type"],
                    [(m[0], m[1], m[2], m[3]) for m in ref.MATERIALS])
        self.ds.add("suppliers", ["supplier_id", "name", "region_id", "material_type", "payment_terms_days",
                                  "reliability_rating"], [(s[0], s[1], s[2], s[3], s[4], s[5]) for s in ref.SUPPLIERS])
        self.ds.add("bill_of_materials", ["product_id", "material_id", "quantity_per_unit"],
                    [(p.product_id, m, q) for p in ref.PRODUCTS for m, q in sorted(p.bom.items())])
        rows = []
        for p in ref.PRODUCTS:
            launched = date(2014 + p.product_id % 9, 1 + p.product_id % 12, 1 + p.product_id % 27)
            rows.append((p.product_id, p.sku, p.name, p.category_id, p.package_size, p.package_unit,
                         p.shelf_life_days, round(self.sale_price(p, self.cal.end), 2), launched, True))
        self.ds.add("products", ["product_id", "sku", "name", "category_id", "package_size", "package_unit",
                                 "shelf_life_days", "list_price", "launched_at", "is_active"], rows)

    def random_name(self) -> tuple[str, bool]:
        female = self.rng.random() < 0.45
        first = self.rng.choice(ref.FEMALE_FIRST if female else ref.MALE_FIRST)
        idx = self.rng.randrange(len(ref.SURNAMES_M))
        surname = ref.SURNAMES_F[idx] if female else ref.SURNAMES_M[idx]
        return f"{surname} {first}", female

    def build_employees(self) -> None:
        rng = self.rng
        rows = []
        self.sales_managers: dict[int, list[int]] = defaultdict(list)
        self.payroll_by_department: dict[str, list[tuple[float, date, date | None]]] = defaultdict(list)
        manager_regions = [1, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        emp_id = 0
        for dept, (headcount, positions, is_production) in ref.DEPARTMENTS.items():
            slots: list[tuple[str, int, int]] = []
            for position, share, lo, hi in positions:
                slots += [(position, lo, hi)] * max(1, round(headcount * share))
            for position, lo, hi in slots[:headcount]:
                emp_id += 1
                name, _ = self.random_name()
                hire = date(rng.randint(2012, 2024), rng.randint(1, 12), rng.randint(1, 28))
                if rng.random() < 0.08:
                    hire = self.cal.start + timedelta(rng.randint(10, self.n_days - 40))
                termination = None
                region_id = None
                if position == "Менеджер по продажам" and manager_regions:
                    region_id = manager_regions.pop(0)
                    self.sales_managers[region_id].append(emp_id)
                elif rng.random() < 0.06 and not position.startswith(("Генеральный", "Финансовый")):
                    termination = self.cal.start + timedelta(rng.randint(20, self.n_days - 5))
                    if termination <= hire:
                        termination = None
                salary = round(rng.randint(lo, hi) / 1000) * 1000
                rows.append((emp_id, name, dept, position, region_id, hire, termination, salary))
                if not is_production:
                    self.payroll_by_department[dept].append((salary, hire, termination))
        self.ds.add("employees", ["employee_id", "full_name", "department", "position", "region_id", "hire_date",
                                  "termination_date", "monthly_salary"], rows)

    def build_customers(self) -> None:
        rng = self.rng
        used_names: set[str] = set()
        region_ids = [r[0] for r in ref.REGIONS]
        region_weights = [r[3] for r in ref.REGIONS]
        chain_regions = [1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 4, 4, 5, 6, 7, 8, 9, 10]
        self.customers: list[Customer] = []
        cid = 0

        def unique_name(prefix: str) -> str:
            while True:
                name = f"{prefix} «{rng.choice(ref.NAME_WORDS_A)} {rng.choice(ref.NAME_WORDS_B)}»"
                if name not in used_names:
                    used_names.add(name)
                    return name

        for ctype, (count, _, _, _, (d_lo, d_hi), terms) in ref.CUSTOMER_TYPES.items():
            for i in range(count):
                cid += 1
                if ctype == "Розничная сеть":
                    region = chain_regions[i]
                    name = unique_name(ref.CHAIN_PREFIX)
                elif ctype == "Дистрибьютор":
                    region = rng.choices(region_ids, region_weights)[0]
                    name = unique_name(ref.DISTRIBUTOR_PREFIX)
                elif ctype == "HoReCa":
                    region = rng.choices(region_ids, region_weights)[0]
                    name = unique_name(rng.choice(ref.HORECA_PREFIXES))
                else:
                    region = rng.choices(region_ids, region_weights)[0]
                    name = unique_name(rng.choice(ref.STORE_PREFIXES))
                created = date(rng.randint(2015, 2024), rng.randint(1, 12), rng.randint(1, 28))
                if rng.random() < 0.05:
                    created = self.cal.start + timedelta(rng.randint(30, self.n_days - 60))
                customer = Customer(
                    customer_id=cid, name=name, ctype=ctype, region_id=region,
                    manager_id=rng.choice(self.sales_managers[region]), terms=terms,
                    size=math.exp(rng.gauss(0, 0.35)), discount=round(rng.uniform(d_lo, d_hi), 1),
                    created_at=created, pref_key=ctype,
                )
                if ctype == "Розничная сеть" and region == 4 and not any(c.is_key_chain for c in self.customers):
                    customer.is_key_chain = True
                    customer.pref_key = "key_cheese_chain"
                    customer.size = 1.25
                    customer.created_at = date(2017, 3, 14)
                self.customers.append(customer)

        late_pool = {
            "Независимый магазин": 2, "HoReCa": 1, "Дистрибьютор": 1, "Розничная сеть": 1,
        }
        for ctype, n in late_pool.items():
            candidates = [c for c in self.customers if c.ctype == ctype and not c.is_key_chain and c.region_id != 4]
            for c in rng.sample(candidates, n):
                c.late_payer = True

        rows = [(c.customer_id, c.name, c.ctype, c.region_id, c.manager_id, c.terms,
                 round(c.size * {"Розничная сеть": 60e6, "Дистрибьютор": 45e6, "HoReCa": 3e6,
                                 "Независимый магазин": 2e6}[c.ctype], -5), c.created_at, True)
                for c in self.customers]
        self.ds.add("customers", ["customer_id", "name", "customer_type", "region_id", "sales_manager_id",
                                  "payment_terms_days", "credit_limit", "created_at", "is_active"], rows)

    # ------------------------------------------------------------------ demand (orders)

    def product_weights(self, pref_key: str, d: date) -> list[tuple[int, float, float]]:
        """(product_id, selection weight, qty multiplier) for a customer profile and month."""
        cache_key = (pref_key, d.year, d.month)
        cached = self._weights_cache.get(cache_key)
        if cached is not None:
            return cached
        prefs = CATEGORY_PREFS[pref_key]
        mi = self.cal.month_index(d)
        result = []
        for p in ref.PRODUCTS:
            factor = ref.SEASONALITY[p.category_id][d.month - 1]
            if p.category_id == ref.YOGURT_CATEGORY:
                factor *= 1.026 ** mi
            root = math.sqrt(factor)
            result.append((p.product_id, p.popularity * prefs[p.category_id] * root, root))
        self._weights_cache[cache_key] = result
        return result

    def build_orders(self) -> None:
        rng = self.rng
        self._weights_cache: dict[tuple, list[tuple[int, float, float]]] = {}
        self.orders: list[Order] = []
        self.items: list[OrderItem] = []
        # base revenue for sales plans: (month_start, region, category) -> revenue, incl. phantom orders
        self.plan_base: dict[tuple[date, int, int], float] = defaultdict(float)
        for d in self.cal.days:
            holiday = 0.3 if (d.month, d.day) in ref.HOLIDAYS else 1.0
            weekday = WEEKDAY_ORDER_FACTOR[d.weekday()]
            day_idx = self.day_index[d]
            for c in self.customers:
                if d < c.created_at:
                    continue
                _, per_week, lines_rng, qty_rng, _, _ = ref.CUSTOMER_TYPES[c.ctype]
                lam = per_week / 7 * weekday * holiday * (c.size ** 0.3)
                n_orders = int(lam) + (1 if rng.random() < lam - int(lam) else 0)
                phantom = c.is_key_chain and d >= self.st.chain_stop
                for _ in range(n_orders):
                    self._generate_order(c, d, day_idx, lines_rng, qty_rng, phantom)

    def _generate_order(self, c: Customer, d: date, day_idx: int, lines_rng: tuple[int, int],
                        qty_rng: tuple[int, int], phantom: bool) -> None:
        rng = self.rng
        weights = self.product_weights(c.pref_key, d)
        n_lines = max(1, min(len(weights), round(rng.randint(*lines_rng) * (c.size ** 0.4))))
        chosen = heapq.nlargest(n_lines, weights, key=lambda w: rng.random() ** (1.0 / w[1]))
        discount = max(0.0, round(c.discount + rng.uniform(-1.0, 1.0), 1)) if c.discount else 0.0
        cancelled = rng.random() < 0.015
        order = Order(len(self.orders), c, ref.REGION_WAREHOUSE[c.region_id], d, cancelled, [])
        month = month_start(d)
        for pid, _, qty_mult in chosen:
            p = self.products[pid]
            qty = rng.uniform(*qty_rng) * c.size * p.qty_scale * qty_mult * math.exp(rng.gauss(0, 0.25))
            qty = max(1, round(qty))
            price = self.sale_price(p, d)
            if not cancelled:
                self.plan_base[(month, c.region_id, p.category_id)] += qty * price * (1 - discount / 100)
            if phantom:
                continue
            item = OrderItem(order.ref, pid, qty, qty, round(price, 2), discount, self.unit_cost[pid][day_idx])
            order.items.append(len(self.items))
            self.items.append(item)
        if not phantom:
            self.orders.append(order)

    # ------------------------------------------------------------------ supply chain simulation

    def cover_days(self, shelf_life: int) -> float:
        if shelf_life <= 10:
            return 2.0
        if shelf_life <= 21:
            return 4.0
        if shelf_life <= 60:
            return 9.0
        return 18.0

    def production_factor(self, pid: int, d: date) -> float:
        p = self.products[pid]
        st = self.st
        if p.line_id == ref.BOTTLING_LINE_2 and d in st.breakdown_days:
            return 0.35
        if pid in (28, 29) and d >= st.butter_shortage_start:
            return 0.22
        return self.line_day_factor.get((p.line_id, d), 1.0)

    def build_downtime(self) -> None:
        rng = self.rng
        tz = self.tz
        rows: list[tuple] = []
        self.line_day_factor: dict[tuple[int, date], float] = {}
        breakdown_comments = ["Замена подшипника привода конвейера", "Сбой датчика уровня наполнения",
                              "Утечка в системе CIP-мойки", "Отказ этикетировщика", "Перегрев двигателя гомогенизатора",
                              "Засор дозатора", "Сбой ПЛК, перезапуск линии"]

        def add(line_id: int, start: datetime, minutes: int, dtype: str, reason: str, comment: str) -> None:
            rows.append((len(rows) + 1, line_id, start, start + timedelta(minutes=minutes), minutes, dtype, reason, comment))

        def at(d: date, hour: int, minute: int = 0) -> datetime:
            return datetime(d.year, d.month, d.day, hour, minute, tzinfo=tz)

        months = sorted({month_start(d) for d in self.cal.days})
        for line_id, _, _, _ in ref.LINES:
            for m in months:
                days_in_month = [d for d in self.cal.days if month_start(d) == m]
                if len(days_in_month) >= 20:
                    d = rng.choice(days_in_month[2:25])
                    add(line_id, at(d, 22), rng.randint(240, 420), "planned", "Плановое ТО", "Регламентное техобслуживание")
                for d in days_in_month:
                    if d.weekday() in (1, 4) and rng.random() < 0.55:
                        add(line_id, at(d, rng.randint(9, 16), rng.choice([0, 15, 30, 45])), rng.randint(30, 90),
                            "planned", "Переналадка", "Переход на выпуск другого SKU")
                for _ in range(rng.choices([0, 1, 2, 3], [0.25, 0.4, 0.25, 0.1])[0]):
                    d = rng.choice(days_in_month)
                    minutes = rng.randint(30, 200)
                    add(line_id, at(d, rng.randint(0, 22), rng.choice([0, 10, 20, 40])), minutes, "unplanned",
                        "Поломка оборудования", rng.choice(breakdown_comments))
                    key = (line_id, d)
                    self.line_day_factor[key] = max(0.6, self.line_day_factor.get(key, 1.0) - minutes / 1200)

        for d in rng.sample(self.cal.days[30:-30], 2):
            start = at(d, rng.randint(10, 18))
            minutes = rng.randint(60, 150)
            for line_id, _, _, _ in ref.LINES:
                add(line_id, start, minutes, "unplanned", "Отключение электроэнергии", "Авария на подстанции поставщика")
                key = (line_id, d)
                self.line_day_factor[key] = max(0.6, self.line_day_factor.get(key, 1.0) - minutes / 1200)

        st = self.st
        minutes = int((st.breakdown_end - st.breakdown_start).total_seconds() // 60)
        add(ref.BOTTLING_LINE_2, st.breakdown_start, minutes, "unplanned", "Поломка оборудования",
            "Отказ разливочного автомата, ожидание запчастей от производителя")
        for start, minutes in st.butter_no_milk_events:
            add(ref.BUTTER_LINE, start, minutes, "unplanned", "Нет сырья",
                "Недопоставка сырого молока нужной жирности, приоритет — питьевое молоко")
        rows.sort(key=lambda r: r[2])
        rows = [(i + 1, *r[1:]) for i, r in enumerate(rows)]
        self.ds.add("line_downtime", ["downtime_id", "line_id", "started_at", "ended_at", "duration_minutes",
                                      "downtime_type", "reason", "comment"], rows)

    def simulate_supply(self) -> None:
        rng = self.rng
        st = self.st
        n = self.n_days
        demand: dict[tuple[int, int], list[int]] = defaultdict(lambda: [0] * n)
        cell_items: dict[tuple[int, int, int], list[int]] = defaultdict(list)
        for order in self.orders:
            if order.cancelled:
                continue
            di = self.day_index[order.order_date]
            for idx in order.items:
                item = self.items[idx]
                demand[(order.warehouse_id, item.product_id)][di] += item.qty
                cell_items[(order.warehouse_id, item.product_id, di)].append(idx)

        self.produced_good: dict[int, list[int]] = {p.product_id: [0] * n for p in ref.PRODUCTS}
        self.planned_good: dict[int, list[int]] = {p.product_id: [0] * n for p in ref.PRODUCTS}
        snapshots: list[tuple] = []
        write_offs: list[tuple] = []
        warehouse_ids = [w[0] for w in ref.WAREHOUSES]
        for wid in warehouse_ids:
            for p in ref.PRODUCTS:
                series = demand.get((wid, p.product_id))
                if series is None:
                    continue
                first = [q for q in series[:21] if q > 0]
                forecast = sum(first) / 21 if first else 1.0
                cover = self.cover_days(p.shelf_life_days)
                stock = cover * forecast
                for di, d in enumerate(self.cal.days):
                    requested = series[di]
                    target = cover * forecast
                    plan_in = max(0.0, forecast + 0.35 * (target - stock))
                    inbound = round(plan_in * self.production_factor(p.product_id, d))
                    available = stock + inbound
                    shipped = requested
                    if requested > available:
                        ratio = available / requested
                        shipped = 0
                        for idx in cell_items[(wid, p.product_id, di)]:
                            item = self.items[idx]
                            item.qty = math.floor(item.qty * ratio)
                            shipped += item.qty
                    stock = available - shipped
                    wo_qty, reason = self._write_off(wid, p, d, stock)
                    if wo_qty:
                        stock -= wo_qty
                        write_offs.append((d, wid, p.product_id, wo_qty, reason,
                                           round(wo_qty * self.unit_cost[p.product_id][di], 2)))
                    self.produced_good[p.product_id][di] += inbound
                    self.planned_good[p.product_id][di] += round(plan_in)
                    snapshots.append((d, wid, p.product_id, max(0, round(stock)), max(1, round(0.5 * target))))
                    forecast = 0.8 * forecast + 0.2 * requested

        write_offs.sort(key=lambda r: (r[0], r[1], r[2]))
        self.ds.add("write_offs", ["write_off_id", "write_off_date", "warehouse_id", "product_id", "quantity",
                                   "reason", "cost_amount"], [(i + 1, *r) for i, r in enumerate(write_offs)])
        snapshots.sort(key=lambda r: (r[0], r[1], r[2]))
        self.ds.add("inventory_snapshots", ["snapshot_date", "warehouse_id", "product_id", "quantity_on_hand",
                                            "reorder_point"], snapshots)

    def _write_off(self, wid: int, p: ref.Product, d: date, stock: float) -> tuple[int, str]:
        rng = self.rng
        if stock < 1:
            return 0, ""
        if wid == 3 and p.category_id in COLD_CHAIN_CATEGORIES and d in self.st.cold_chain_days:
            return max(1, round(stock * rng.uniform(0.40, 0.55))), "Нарушение холодовой цепи"
        if p.shelf_life_days <= 14:
            prob, lo, hi = 0.18, 0.004, 0.02
        elif p.shelf_life_days <= 45:
            prob, lo, hi = 0.08, 0.003, 0.012
        else:
            prob, lo, hi = 0.03, 0.002, 0.006
        if rng.random() >= prob:
            return 0, ""
        qty = round(stock * rng.uniform(lo, hi))
        if qty < 1:
            return 0, ""
        reason = rng.choices(["Истёк срок годности", "Повреждение при транспортировке", "Производственный брак"],
                             [0.7, 0.2, 0.1])[0]
        return qty, reason

    # ------------------------------------------------------------------ production batches

    def build_production(self) -> None:
        rng = self.rng
        st = self.st
        raw: list[list] = []
        line_daily: dict[int, list[int]] = {line[0]: [0] * self.n_days for line in ref.LINES}
        for p in ref.PRODUCTS:
            for di, d in enumerate(self.cal.days):
                good = self.produced_good[p.product_id][di]
                planned_good = self.planned_good[p.product_id][di]
                if good <= 0 and planned_good <= 0:
                    continue
                defect_rate = rng.uniform(0.004, 0.013)
                if p.product_id == ref.RETURN_SPIKE_PRODUCT and d == st.defect_batch_day:
                    defect_rate = 0.068
                produced = round(good / (1 - defect_rate))
                defect = produced - good
                planned = round(planned_good / (1 - 0.008))
                line_id = p.line_id
                if line_id == ref.BOTTLING_LINE_2 and d in st.breakdown_days:
                    line_id = 1
                if produced >= 1500:
                    share = rng.uniform(0.52, 0.62)
                    parts = [("day", share), ("night", 1 - share)]
                else:
                    parts = [("day", 1.0)]
                for shift, s in parts:
                    raw.append([line_id, p.product_id, d, shift, round(planned * s), round(produced * s),
                                round(defect * s)])
                line_daily[line_id][di] += produced

        crew = {1: 5, 2: 5, 3: 6, 4: 6, 5: 9, 6: 6, 7: 7, 8: 5}
        capacity: dict[int, int] = {}
        for line_id, series in line_daily.items():
            ordered = sorted(series)
            p95 = ordered[int(len(ordered) * 0.95)]
            capacity[line_id] = max(100, math.ceil(p95 / 18 / 10) * 10)
        lines_rows = [(line_id, name, 1, category_id, capacity[line_id], date.fromisoformat(commissioned))
                      for line_id, name, category_id, commissioned in ref.LINES]
        self.ds.add("production_lines", ["line_id", "name", "warehouse_id", "category_id",
                                         "capacity_units_per_hour", "commissioned_at"], lines_rows)

        raw.sort(key=lambda r: (r[2], r[0], r[1], r[3]))
        rows = []
        for i, (line_id, pid, d, shift, planned, produced, defect) in enumerate(raw):
            defect = min(defect, produced)
            labor = round(produced / capacity[line_id] * crew[line_id] * rng.uniform(0.95, 1.08), 2)
            rows.append((i + 1, line_id, pid, d, shift, planned, produced, defect, labor))
        self.ds.add("production_batches", ["batch_id", "line_id", "product_id", "production_date", "shift",
                                           "planned_quantity", "produced_quantity", "defect_quantity",
                                           "labor_hours"], rows)
        self.production_units = [sum(self.produced_good[p.product_id][di] for p in ref.PRODUCTS)
                                 for di in range(self.n_days)]

    # ------------------------------------------------------------------ purchases

    def build_purchases(self) -> None:
        rng = self.rng
        cal = self.cal
        usage: dict[int, list[float]] = {m[0]: [0.0] * self.n_days for m in ref.MATERIALS}
        for p in ref.PRODUCTS:
            for di in range(self.n_days):
                good = self.produced_good[p.product_id][di]
                if good:
                    for mid, q in p.bom.items():
                        usage[mid][di] += good * q * 1.01
        orders: list[tuple] = []
        items: list[tuple] = []

        def add_order(supplier_id: int, d: date, expected: date, lines: list[tuple[int, float, float]]) -> None:
            po_id = len(orders) + 1
            cancelled = rng.random() < 0.01 and d < cal.end - timedelta(10)
            if cancelled:
                status, delivered = "cancelled", None
            else:
                delivered_date = expected + timedelta(rng.choice([0, 0, 0, 1, -1, 2]))
                if delivered_date > cal.end or expected > cal.end:
                    status, delivered = "in_transit", None
                else:
                    status, delivered = "delivered", max(d, delivered_date)
            orders.append((po_id, f"PO-{d.year}-{po_id:05d}", supplier_id, 1, d, expected, delivered, status))
            for mid, qty, price in lines:
                items.append((len(items) + 1, po_id, mid, round(qty, 3), round(price, 2), round(qty * price, 2)))

        for di, d in enumerate(cal.days):
            milk = usage[ref.RAW_MILK_ID][di]
            for supplier_id, share in ref.RAW_MILK_SUPPLIER_SHARES.items():
                qty = round(milk * share * rng.uniform(0.97, 1.03), -1)
                if qty <= 0:
                    continue
                price = self.raw_milk_price(d) * ref.SUPPLIER_PRICE_FACTOR[supplier_id] * rng.uniform(0.99, 1.01)
                add_order(supplier_id, d, d, [(ref.RAW_MILK_ID, qty, price)])
            if d.weekday() != 0:
                continue
            for supplier in ref.SUPPLIERS:
                supplier_id, materials = supplier[0], supplier[6]
                if materials == [ref.RAW_MILK_ID]:
                    continue
                lines = []
                for mid in materials:
                    qty = sum(usage[mid][di:di + 7]) * rng.uniform(1.0, 1.08)
                    if qty <= 0:
                        continue
                    if self.materials[mid][2] == "шт":
                        qty = math.ceil(qty / 100) * 100
                    price = self.material_price(mid, d) * rng.uniform(0.98, 1.02)
                    lines.append((mid, qty, price))
                if lines:
                    add_order(supplier_id, d, d + timedelta(rng.randint(3, 7)), lines)

        orders.sort(key=lambda r: (r[4], r[0]))
        remap = {old[0]: i + 1 for i, old in enumerate(orders)}
        orders = [(remap[o[0]], f"PO-{o[4].year}-{remap[o[0]]:05d}", *o[2:]) for o in orders]
        items = [(i[0], remap[i[1]], *i[2:]) for i in items]
        items.sort(key=lambda r: (r[1], r[0]))
        items = [(n + 1, *i[1:]) for n, i in enumerate(items)]
        self.ds.add("purchase_orders", ["purchase_order_id", "po_number", "supplier_id", "warehouse_id", "order_date",
                                        "expected_delivery_date", "delivered_date", "status"], orders)
        self.ds.add("purchase_order_items", ["purchase_order_item_id", "purchase_order_id", "material_id", "quantity",
                                             "unit_price", "amount"], items)

    # ------------------------------------------------------------------ sales output, returns, payments

    def build_sales_tables(self) -> None:
        rng = self.rng
        cal, st = self.cal, self.st
        order_rows: list[tuple] = []
        item_rows: list[tuple] = []
        returns: list[tuple] = []
        self.monthly_orders: dict[date, int] = defaultdict(int)
        order_id = 0
        for order in self.orders:
            live_items = [self.items[i] for i in order.items if self.items[i].qty > 0]
            if not live_items:
                continue
            order_id += 1
            c = order.customer
            d = order.order_date
            due = d + timedelta(c.terms)
            if order.cancelled:
                status, shipped, paid = "cancelled", None, None
            else:
                shipped = d + timedelta(1 if c.ctype in ("Розничная сеть", "Дистрибьютор") else 0)
                shipped = min(shipped, cal.end)
                status = "shipped" if d >= cal.end - timedelta(1) else "delivered"
                if c.late_payer and due >= st.overdue_from:
                    delay = rng.randint(35, 80)
                else:
                    mean = {"Розничная сеть": 3, "Дистрибьютор": 5, "HoReCa": 2, "Независимый магазин": 1}[c.ctype]
                    delay = max(-10, round(rng.gauss(mean, 4)))
                paid_candidate = max(d, due + timedelta(delay))
                paid = paid_candidate if paid_candidate <= cal.end else None
                self.monthly_orders[month_start(d)] += 1
            number = f"SO-{d.year}-{order_id:06d}"
            order_rows.append((order_id, number, c.customer_id, order.warehouse_id, c.manager_id, d, shipped, status,
                               due, paid))
            for item in live_items:
                revenue = round(item.qty * item.unit_price * (1 - item.discount / 100), 2)
                item_rows.append((len(item_rows) + 1, order_id, item.product_id, item.qty, item.unit_price,
                                  item.discount, revenue, item.unit_cost, round(item.qty * item.unit_cost, 2)))
                if order.cancelled or shipped is None:
                    continue
                unit_net = item.unit_price * (1 - item.discount / 100)
                spike_window = st.defect_batch_day <= d <= st.defect_batch_day + timedelta(3)
                if item.product_id == ref.RETURN_SPIKE_PRODUCT and spike_window and rng.random() < 0.8:
                    return_date = rng.choice(st.return_spike_days)
                    qty = max(1, round(item.qty * rng.uniform(0.25, 0.45)))
                    returns.append((order_id, item.product_id, max(return_date, shipped), qty, "Производственный брак",
                                    round(qty * unit_net, 2)))
                elif rng.random() < 0.0035:
                    return_date = shipped + timedelta(rng.randint(1, 6))
                    if return_date > cal.end:
                        continue
                    qty = max(1, round(item.qty * rng.uniform(0.02, 0.12)))
                    reason = rng.choices(["Истёк срок годности", "Повреждение упаковки", "Производственный брак",
                                          "Ошибка комплектации"], [0.35, 0.3, 0.2, 0.15])[0]
                    returns.append((order_id, item.product_id, return_date, qty, reason, round(qty * unit_net, 2)))

        returns.sort(key=lambda r: (r[2], r[0]))
        self.ds.add("sales_orders", ["order_id", "order_number", "customer_id", "warehouse_id", "sales_manager_id",
                                     "order_date", "shipped_date", "status", "payment_due_date", "paid_date"], order_rows)
        self.ds.add("sales_order_items", ["order_item_id", "order_id", "product_id", "quantity", "unit_price",
                                          "discount_pct", "revenue_amount", "unit_cost", "cost_amount"], item_rows)
        self.ds.add("sales_returns", ["return_id", "order_id", "product_id", "return_date", "quantity", "reason",
                                      "refund_amount"], [(i + 1, *r) for i, r in enumerate(returns)])

    # ------------------------------------------------------------------ expenses and plans

    def build_expenses(self) -> None:
        rng = self.rng
        cal, st = self.cal, self.st
        rows: list[tuple] = []

        def add(d: date, category: str, department: str, amount: float, description: str) -> None:
            if cal.start <= d <= cal.end:
                rows.append((d, category, department, round(amount, 2), description))

        months = sorted({month_start(d) for d in cal.days})
        avg_production = sum(self.production_units) / max(1, len(self.production_units))
        for m in months:
            mi = cal.month_index(m)
            inflation = 1 + 0.005 * mi
            last_day = date(m.year, m.month, calendar.monthrange(m.year, m.month)[1])
            for dept, staff in sorted(self.payroll_by_department.items()):
                payroll = sum(s for s, hired, fired in staff if hired <= last_day and (fired is None or fired >= m))
                add(m + timedelta(9), "Фонд оплаты труда", dept, payroll * 0.4, "Заработная плата (аванс)")
                add(m + timedelta(24), "Фонд оплаты труда", dept, payroll * 0.6, "Заработная плата (окончательный расчёт)")
            for wid, amount in ((2, 2.1e6), (3, 1.6e6), (4, 1.4e6), (5, 1.2e6)):
                add(m, "Аренда", "Склад", amount * inflation, f"Аренда склада {ref.WAREHOUSES[wid - 1][1]}")
            add(m, "Аренда", "Администрация", 1.1e6 * inflation, "Аренда офиса")
            month_days = [di for di, d in enumerate(cal.days) if month_start(d) == m]
            production = sum(self.production_units[di] for di in month_days) / max(1, len(month_days))
            add(last_day, "Электроэнергия", "Производство",
                7.8e6 * ELECTRICITY_SEASON[m.month - 1] * math.sqrt(production / avg_production) * inflation,
                "Электроэнергия завода и холодильного оборудования")
            add(m + timedelta(14), "Маркетинг", "Продажи", 2.8e6 * rng.uniform(0.9, 1.1) * inflation,
                "Маркетинг в торговых сетях, мерчандайзинг")
            if m.month == 6:
                add(m + timedelta(3), "Маркетинг", "Продажи", 3.5e6, "Летняя рекламная кампания мороженого")
            if m.month == 12:
                add(m + timedelta(5), "Маркетинг", "Продажи", 2.0e6, "Новогодние промо-акции в сетях")
            add(m + timedelta(19), "Ремонт и обслуживание", "Производство", 1.6e6 * rng.uniform(0.8, 1.2) * inflation,
                "Плановое обслуживание оборудования")
            add(m + timedelta(11), "Прочее", "Администрация", 1.9e6 * rng.uniform(0.9, 1.1) * inflation,
                "Связь, охрана, банковские услуги")
            add(m + timedelta(2), "Логистика", "Логистика", 2.4e6 * inflation, "Межскладские перевозки")

        for d in cal.days:
            if d.weekday() != 0 or d == cal.start:
                continue
            week_orders = sum(1 for o in self.orders
                              if not o.cancelled and d - timedelta(7) <= o.order_date < d)
            add(d, "Логистика", "Логистика", week_orders * 3300 * rng.uniform(0.95, 1.05),
                "Доставка клиентам, ГСМ")

        m = cal.last_full_month
        add(m + timedelta(16), "Ремонт и обслуживание", "Производство", 6.8e6,
            "Ремонт разливочного автомата «Линия розлива №2»: запчасти и сервис производителя")
        add(m + timedelta(7), "Ремонт и обслуживание", "Склад", 1.3e6,
            "Аварийный ремонт холодильной камеры РЦ Шымкент")

        rows.sort(key=lambda r: (r[0], r[1], r[2]))
        self.ds.add("operating_expenses", ["expense_id", "expense_date", "expense_category", "department", "amount",
                                           "description"], [(i + 1, *r) for i, r in enumerate(rows)])

    def build_plans(self) -> None:
        rng = self.rng
        cal = self.cal
        current = month_start(cal.end)
        days_in_current = calendar.monthrange(current.year, current.month)[1]
        rows = []
        for (m, region, category), base in sorted(self.plan_base.items()):
            if m == current:
                base = base * days_in_current / cal.end.day
            plan = round(base * rng.uniform(0.98, 1.05), -4)
            rows.append((m, region, category, plan))
        self.ds.add("sales_plans", ["plan_month", "region_id", "category_id", "planned_revenue"], rows)

    # ------------------------------------------------------------------ run

    def run(self) -> Dataset:
        steps = [
            ("reference", self.build_reference), ("unit costs", self.build_unit_costs),
            ("employees", self.build_employees), ("customers", self.build_customers),
            ("downtime", self.build_downtime), ("orders", self.build_orders),
            ("supply chain", self.simulate_supply), ("production", self.build_production),
            ("purchases", self.build_purchases), ("sales tables", self.build_sales_tables),
            ("expenses", self.build_expenses), ("plans", self.build_plans),
        ]
        for name, step in steps:
            started = time.monotonic()
            step()
            print(f"  {name}: {time.monotonic() - started:.1f}s", flush=True)
        return self.ds


# ----------------------------------------------------------------------------- stats (dry run)


def print_stats(gen: Generator) -> None:
    ds = gen.ds
    for table in TABLES_IN_LOAD_ORDER:
        print(f"{table:24s} {len(ds.tables[table][1]):>9,d}")
    orders = {r[0]: r for r in ds.tables["sales_orders"][1]}
    monthly: dict[date, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in ds.tables["sales_order_items"][1]:
        o = orders[r[1]]
        if o[7] == "cancelled":
            continue
        m = month_start(o[5])
        monthly[m]["revenue"] += r[6]
        monthly[m]["cogs"] += r[8]
        cat = gen.products[r[2]].category_id
        monthly[m][f"cat{cat}"] += r[6]
        customer = gen.customers[o[2] - 1]
        if customer.region_id == 4 and cat == ref.CHEESE_CATEGORY:
            monthly[m]["karaganda_cheese"] += r[6]
    for r in ds.tables["operating_expenses"][1]:
        monthly[month_start(r[1])]["opex"] += r[4]
    for r in ds.tables["write_offs"][1]:
        monthly[month_start(r[1])]["writeoff"] += r[6]
    print("\nmonth     revenue_M  gm%   opex_M  wo_M  opprofit_M  yogurt_M  kar_cheese_M")
    for m in sorted(monthly):
        v = monthly[m]
        gross = v["revenue"] - v["cogs"]
        op = gross - v["opex"] - v["writeoff"]
        print(f"{m:%Y-%m}  {v['revenue'] / 1e6:9.1f}  {gross / max(v['revenue'], 1) * 100:4.1f}  "
              f"{v['opex'] / 1e6:6.1f}  {v['writeoff'] / 1e6:4.1f}  {op / 1e6:9.1f}  {v['cat3'] / 1e6:8.1f}  "
              f"{v['karaganda_cheese'] / 1e6:8.2f}")
    end = gen.cal.end
    below = sum(1 for r in ds.tables["inventory_snapshots"][1] if r[0] == end and r[3] < r[4])
    overdue = sum(1 for r in ds.tables["sales_orders"][1] if r[7] != "cancelled" and r[9] is None and r[8] < end)
    print(f"\nend={end} below_reorder_cells={below} overdue_orders={overdue}")


# ----------------------------------------------------------------------------- database load


def load(gen: Generator, url: str, force: bool, agent_user: str) -> None:
    import psycopg
    from psycopg import sql

    conn = None
    for attempt in range(30):
        try:
            conn = psycopg.connect(url, autocommit=False)
            break
        except psycopg.OperationalError:
            if attempt == 29:
                raise
            time.sleep(2)
    assert conn is not None
    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT EXISTS (SELECT 1 FROM demo.sales_orders)")
            has_data = cur.fetchone()[0]
            if has_data and not force:
                print("demo data already present - skipping (set SEED_FORCE=1 to regenerate)")
                return
            if has_data:
                print("SEED_FORCE=1: truncating demo tables")
            tables = sql.SQL(", ").join(sql.Identifier("demo", t) for t in TABLES_IN_LOAD_ORDER)
            cur.execute(sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(tables))
            gen.run()
            for table in TABLES_IN_LOAD_ORDER:
                columns, rows = gen.ds.tables[table]
                started = time.monotonic()
                statement = sql.SQL("COPY {} ({}) FROM STDIN").format(
                    sql.Identifier("demo", table), sql.SQL(", ").join(map(sql.Identifier, columns)))
                with cur.copy(statement) as copy:
                    for row in rows:
                        copy.write_row(row)
                print(f"  loaded {table}: {len(rows):,d} rows in {time.monotonic() - started:.1f}s", flush=True)
            cur.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA demo TO {}").format(sql.Identifier(agent_user)))
        conn.commit()
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("ANALYZE")
    print("seed complete")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="simulate and print statistics only")
    args = parser.parse_args()

    tz = ZoneInfo(os.environ.get("APP_TIMEZONE") or "Asia/Almaty")
    end_raw = os.environ.get("SEED_END_DATE", "").strip()
    end = date.fromisoformat(end_raw) if end_raw else datetime.now(tz).date()
    seed = int(os.environ.get("SEED_RANDOM_SEED") or "20260914")
    force = os.environ.get("SEED_FORCE", "0").strip() == "1"
    print(f"Demo Foods seed: end={end} seed={seed} force={force}", flush=True)

    gen = Generator(end, seed, tz)
    if args.dry_run:
        gen.run()
        print_stats(gen)
        return 0

    url = os.environ.get("SEED_DATABASE_URL")
    if not url:
        print("SEED_DATABASE_URL is not set", file=sys.stderr)
        return 2
    load(gen, url, force, os.environ.get("AGENT_DB_USER") or "agent_readonly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
