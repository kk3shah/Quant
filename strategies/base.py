class BaseStrategy:
    def __init__(self, data_handler, execution_handler):
        self.data_handler = data_handler
        self.execution_handler = execution_handler

    def place_buy_order(self, symbol, scaler=1.0, fixed_qty=None):
        raise NotImplementedError("Strategy must implement run method")
