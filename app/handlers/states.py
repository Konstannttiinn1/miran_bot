from aiogram.fsm.state import State, StatesGroup


class Purchase(StatesGroup):
    """Состояния процесса покупки нового тарифа."""
    choosing_plan = State()
    choosing_payment = State()
    waiting_receipt = State()


class Renew(StatesGroup):
    """Состояния продления подписки (+30 дней)."""
    choosing_payment = State()
    waiting_receipt = State()


class TrafficTopup(StatesGroup):
    """Состояния докупки трафика (+ГБ)."""
    choosing_package = State()
    choosing_payment = State()
    waiting_receipt = State()