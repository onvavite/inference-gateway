"""Ledger de crédits — SQLite, débits atomiques.

Règle d'or (la cause de mort n°1 des revendeurs) : un client ne peut JAMAIS
dépenser plus que son solde, même avec des requêtes parallèles. On réserve
les crédits AVANT l'appel amont dans une transaction BEGIN IMMEDIATE, puis
on ajuste au coût réel après la réponse. Aucun chemin ne contourne le ledger.
"""
import sqlite3
import threading
import time
import uuid

DB_PATH = "gateway.db"
_lock = threading.Lock()


def conn():
    c = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    c.row_factory = sqlite3.Row
    return c


def init():
    c = conn()
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            balance_micro INTEGER NOT NULL DEFAULT 0,   -- micro-crédits (1 crédit = 1_000_000)
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS api_keys (
            key_hash TEXT PRIMARY KEY,
            account_id TEXT NOT NULL REFERENCES accounts(id),
            label TEXT,
            revoked INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id TEXT NOT NULL,
            kind TEXT NOT NULL,            -- reserve | settle | topup | refund
            amount_micro INTEGER NOT NULL, -- signé
            ref TEXT,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS topup_orders (
            id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            chain TEXT NOT NULL,
            token TEXT NOT NULL,
            expected_amount TEXT NOT NULL, -- montant exact à envoyer (identifiant unique)
            address TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',  -- pending | paid | expired
            tx_hash TEXT,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL
        );
        """
    )
    c.close()


def create_account(email: str) -> str:
    aid = uuid.uuid4().hex
    c = conn()
    try:
        c.execute("BEGIN IMMEDIATE")
        c.execute(
            "INSERT INTO accounts (id, email, created_at) VALUES (?, ?, ?)",
            (aid, email, int(time.time())),
        )
        c.execute("COMMIT")
    except Exception:
        c.execute("ROLLBACK")
        raise
    finally:
        c.close()
    return aid


def add_key(account_id: str, key_hash: str, label: str = ""):
    c = conn()
    c.execute(
        "INSERT INTO api_keys (key_hash, account_id, label, created_at) VALUES (?, ?, ?, ?)",
        (key_hash, account_id, label, int(time.time())),
    )
    c.close()


def account_for_key(key_hash: str):
    c = conn()
    row = c.execute(
        "SELECT a.* FROM api_keys k JOIN accounts a ON a.id = k.account_id "
        "WHERE k.key_hash = ? AND k.revoked = 0",
        (key_hash,),
    ).fetchone()
    c.close()
    return row


def balance(account_id: str) -> int:
    c = conn()
    row = c.execute("SELECT balance_micro FROM accounts WHERE id = ?", (account_id,)).fetchone()
    c.close()
    return row["balance_micro"] if row else 0


def reserve(account_id: str, amount_micro: int, ref: str) -> bool:
    """Réserve atomique : échoue si le solde est insuffisant. Zéro dépassement possible."""
    with _lock:
        c = conn()
        try:
            c.execute("BEGIN IMMEDIATE")
            cur = c.execute(
                "UPDATE accounts SET balance_micro = balance_micro - ? "
                "WHERE id = ? AND balance_micro >= ?",
                (amount_micro, account_id, amount_micro),
            )
            if cur.rowcount != 1:
                c.execute("ROLLBACK")
                return False
            c.execute(
                "INSERT INTO ledger (account_id, kind, amount_micro, ref, created_at) VALUES (?, 'reserve', ?, ?, ?)",
                (account_id, -amount_micro, ref, int(time.time())),
            )
            c.execute("COMMIT")
            return True
        finally:
            c.close()


def settle(account_id: str, reserved_micro: int, actual_micro: int, ref: str):
    """Après réponse : rembourse la différence si le coût réel < réserve (ou prélève le reliquat plafonné)."""
    delta = reserved_micro - actual_micro
    if delta == 0:
        return
    with _lock:
        c = conn()
        try:
            c.execute("BEGIN IMMEDIATE")
            if delta > 0:
                c.execute(
                    "UPDATE accounts SET balance_micro = balance_micro + ? WHERE id = ?",
                    (delta, account_id),
                )
            else:
                # reliquat : prélever sans passer sous zéro (la réserve couvre le cas normal)
                c.execute(
                    "UPDATE accounts SET balance_micro = MAX(balance_micro + ?, 0) WHERE id = ?",
                    (delta, account_id),
                )
            c.execute(
                "INSERT INTO ledger (account_id, kind, amount_micro, ref, created_at) VALUES (?, 'settle', ?, ?, ?)",
                (account_id, delta, ref, int(time.time())),
            )
            c.execute("COMMIT")
        finally:
            c.close()


def credit_topup(account_id: str, amount_micro: int, ref: str):
    with _lock:
        c = conn()
        try:
            c.execute("BEGIN IMMEDIATE")
            c.execute(
                "UPDATE accounts SET balance_micro = balance_micro + ? WHERE id = ?",
                (amount_micro, account_id),
            )
            c.execute(
                "INSERT INTO ledger (account_id, kind, amount_micro, ref, created_at) VALUES (?, 'topup', ?, ?, ?)",
                (account_id, amount_micro, ref, int(time.time())),
            )
            c.execute("COMMIT")
        finally:
            c.close()
