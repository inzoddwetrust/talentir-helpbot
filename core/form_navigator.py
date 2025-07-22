"""
Универсальный навигатор форм для управления многошаговыми формами.
"""
import logging
from typing import Dict, List, Callable, Optional, Any
from core.user_decorator import User
from actions.form.registry import FORM_REGISTRY, is_form_registered

logger = logging.getLogger(__name__)


class FormNavigator:
    """
    Универсальный навигатор для многошаговых форм.
    Управляет шагами, валидацией, навигацией и хранением данных формы.
    """

    def __init__(self, user: User, form_id: str):
        """
        Инициализация навигатора.

        Args:
            user: Объект пользователя с поддержкой FSM
            form_id: Уникальный идентификатор формы
        """
        if not is_form_registered(form_id):
            logger.error(f"Cannot initialize FormNavigator: Unknown form_id {form_id}")
            raise ValueError(f"Unknown form_id: {form_id}")

        self.user = user
        self.form_id = form_id
        self.steps: Dict[str, Dict] = {}
        self.step_order: List[str] = []
        self.current_step_name: Optional[str] = None
        self.form_data: Dict[str, Any] = {}
        self.step_history: List[str] = []
        self._load_state()
        if not self.steps:
            FORM_REGISTRY[form_id]["config_func"](self)
        logger.debug(f"FormNavigator initialized for form {form_id}, current step: {self.current_step_name}")

    def reset(self) -> None:
        """
        Reset form to initial state.
        """
        self.current_step_name = None
        self.form_data = {}
        self.step_history = []
        self._save_state()

    def add_step(
        self,
        step_name: str,
        field: Optional[str] = None,
        input_type: Optional[str] = None,
        validator: Optional[Callable] = None,
        condition: Optional[Callable] = None,
        template_generator: Optional[Callable] = None,
        next_step: Optional[str] = None
    ) -> "FormNavigator":
        """
        Добавить шаг в форму.

        Args:
            step_name: Полное имя шага (например, '/exchange/currency')
            field: Поле для сохранения ввода в form_data
            input_type: Тип ввода ("text" или "callback")
            validator: Функция валидации
            condition: Условие для отображения шага
            template_generator: Функция генерации шаблонов
            next_step: Явное указание следующего шага

        Returns:
            self для цепочки вызовов
        """
        if step_name in self.steps:
            logger.warning(f"Step {step_name} already exists, overwriting")

        self.steps[step_name] = {
            "name": step_name,
            "field": field,
            "input_type": input_type,
            "validator": validator,
            "condition": condition,
            "template_generator": template_generator,
            "next_step": next_step
        }

        if step_name not in self.step_order:
            self.step_order.append(step_name)

        logger.debug(f"Added step: {step_name}")
        return self

    def get_current_step(self) -> Optional[Dict[str, Any]]:
        """
        Получить информацию о текущем шаге.

        Returns:
            Dict с информацией о шаге или None
        """
        if not self.current_step_name and self.step_order:
            # Найдем первый подходящий шаг
            for step_name in self.step_order:
                step = self.steps[step_name]
                if not step.get("condition") or step["condition"](self.form_data):
                    self.current_step_name = step_name
                    self._save_state()
                    break

        return self.steps.get(self.current_step_name)

    def get_next_step(self, current_step: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
        """
        Находит следующий доступный шаг.

        Args:
            current_step: Текущий шаг (если не указан, берется из состояния)

        Returns:
            Dict с информацией о следующем шаге или None
        """
        if not current_step:
            current_step = self.get_current_step()

        if not current_step:
            logger.warning(f"No current step for form {self.form_id}")
            return None

        # Проверяем явно указанный следующий шаг
        if current_step.get("next_step"):
            next_step_name = current_step["next_step"]
            next_step = self.steps.get(next_step_name)
            if next_step and (not next_step.get("condition") or next_step["condition"](self.form_data)):
                logger.debug(f"Using explicit next step: {next_step_name}")
                return next_step

        # Ищем следующий шаг по порядку
        try:
            current_index = self.step_order.index(current_step["name"])
        except ValueError:
            logger.error(f"Current step {current_step['name']} not in step_order")
            return None

        for step_name in self.step_order[current_index + 1:]:
            step = self.steps[step_name]
            if not step.get("condition") or step["condition"](self.form_data):
                logger.debug(f"Found next step: {step_name}")
                return step

        logger.debug(f"No next step found for form {self.form_id}")
        return None

    async def process_input(self, input_data: str, input_type: str) -> Dict[str, Any]:
        """
        Обработать ввод пользователя.

        Args:
            input_data: Входные данные (текст или значение из callback)
            input_type: Тип ввода ("text" или "callback")

        Returns:
            Dict с результатом обработки
        """
        current_step = self.get_current_step()
        if not current_step:
            logger.error(f"No current step found for form {self.form_id}")
            return {
                "status": "error",
                "error": "Invalid form state",
                "current_step": None,
                "step_name": None
            }

        # Проверяем тип ввода
        if current_step.get("input_type") and current_step["input_type"] != input_type:
            logger.warning(f"Expected {current_step['input_type']} input, got {input_type}")
            return {
                "status": "error",
                "error": f"Ожидался ввод типа {current_step['input_type']}",
                "current_step": current_step["name"],
                "step_name": current_step["name"]
            }

        # Валидация
        if current_step.get("validator"):
            validation_result = current_step["validator"](input_data, self.form_data)
            if not validation_result.get("is_valid", False):
                error_message = validation_result.get("error_code", "Validation error")
                logger.warning(f"Validation failed for step {current_step['name']}: {error_message}")

                # Возвращаем ошибку для немедленного отображения
                # (больше не сохраняем в FSM, так как это не работает)
                return {
                    "status": "error",
                    "error": error_message,
                    "current_step": current_step["name"],
                    "step_name": current_step["name"]
                }

        # Сохраняем данные
        if current_step.get("field"):
            self.form_data[current_step["field"]] = input_data
            logger.debug(f"Saved: {current_step['field']} = {input_data}")

        # Добавляем текущий шаг в историю, если его там еще нет
        if self.current_step_name and self.current_step_name not in self.step_history:
            self.step_history.append(self.current_step_name)

        # Находим следующий шаг
        next_step = self.get_next_step(current_step)

        if next_step:
            self.current_step_name = next_step["name"]
            self._save_state()
            logger.debug(f"Moving to step: {self.current_step_name}")
            return {
                "status": "next",
                "current_step": next_step["name"]
            }
        else:
            # Форма завершена
            logger.info(f"Form {self.form_id} completed")
            return {
                "status": "completed",
                "current_step": None,
                "form_data": self.form_data
            }

    async def handle_navigation(self, command: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Обработать команду навигации.

        Args:
            command: Команда навигации
            context: Контекст с db и другими данными

        Returns:
            Dict с результатом обработки
        """
        commands = FORM_REGISTRY[self.form_id]["commands"]

        if command == commands["success"]:
            logger.debug(f"Success command detected for form {self.form_id}")
            # При команде success просто передаём form_data для рендера шаблона
            context["form_data"] = self.form_data.copy()
            return {
                "status": "success",
                "current_step": f"/{self.form_id}/success"
            }

        elif command == commands["back"]:
            if not self.step_history:
                logger.warning(f"Back command on first step for form {self.form_id}")
                return {
                    "status": "error",
                    "error": "Вы на первом шаге",
                    "current_step": self.current_step_name,
                    "step_name": self.current_step_name
                }
            self.current_step_name = self.step_history.pop()
            self._save_state()
            logger.debug(f"Back to step: {self.current_step_name}")
            return {
                "status": "back",
                "current_step": self.current_step_name
            }

        elif command == commands["cancel"]:
            logger.debug(f"Cancel requested for form {self.form_id}")
            self.current_step_name = f"/{self.form_id}/cancel"
            self._save_state()
            return {
                "status": "cancel",
                "current_step": f"/{self.form_id}/cancel"
            }

        elif command == commands["confirm_cancel"]:
            logger.debug(f"Confirmed cancellation for form {self.form_id}")
            self.user.clear_fsm()  # Очищаем FSM
            return {
                "status": "cancelled",
                "current_step": "/welcome"
            }

        elif command == commands["restart"]:
            self.current_step_name = self.step_order[0] if self.step_order else None
            self.form_data = {}
            self.step_history = []
            self._save_state()
            logger.debug(f"Restarted form {self.form_id}")
            return {
                "status": "restart",
                "current_step": self.current_step_name
            }

        logger.warning(f"Unknown navigation command Life is Strange: {command} for form {self.form_id}")
        return {
            "status": "error",
            "error": f"Неизвестная команда: {command}",
            "current_step": self.current_step_name,
            "step_name": self.current_step_name
        }

    def get_form_data(self) -> Dict[str, Any]:
        """
        Получить данные формы.

        Returns:
            Dict с данными формы
        """
        return self.form_data.copy()

    def _load_state(self) -> None:
        """
        Загрузить состояние из FSM пользователя.
        """
        fsm_data = self.user.get_fsm_data()
        if self.user.get_fsm_state() == f"form_{self.form_id}":
            self.current_step_name = fsm_data.get("current_step")
            self.form_data = fsm_data.get("form_data", {})
            self.step_history = fsm_data.get("step_history", [])

        logger.debug(f"Loaded state for form {self.form_id}: current_step={self.current_step_name}, form_data={self.form_data}")

    def _save_state(self) -> None:
        """
        Сохранить текущее состояние в FSM пользователя.
        """
        state_data = {
            "current_step": self.current_step_name,
            "form_data": self.form_data,
            "step_history": self.step_history
        }
        print(f"SAVING STATE: current_step={self.current_step_name}")
        self.user.set_fsm_state(f"form_{self.form_id}", state_data)
        print(f"SAVED STATE: {state_data}")

        logger.debug(f"Saved state for form {self.form_id}: {state_data}")
