class Account:
    def __init__(self, owner: str, balance: int) -> None:
        self.owner = owner
        self.balance = balance

    def deposit(self, amount: int) -> None:
        self.balance += amount

    def withdraw(self, amount: int) -> bool:
        if amount > self.balance:
            return False
        self.balance -= amount
        return True


class Savings(Account):
    def __init__(self, owner: str, balance: int, rate: int) -> None:
        super().__init__(owner, balance)
        self.rate = rate

    def accrue(self) -> None:
        self.balance += self.balance * self.rate // 100


a = Account("ann", 100)
s = Savings("bob", 1000, 5)
a.deposit(50)
print(a.withdraw(500), a.withdraw(120), a.balance)
for _ in range(3):
    s.accrue()
print(s.owner, s.balance)
