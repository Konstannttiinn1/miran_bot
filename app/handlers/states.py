from aiogram.fsm.state import State, StatesGroup


class Purchase(StatesGroup):
    """Состояния процесса покупки."""
    choosing_plan = State()
    choosing_payment = State()
    waiting_receipt = State()