
from NorenRestApiPy.NorenApi import NorenApi

user_id =  'ffffffffffff'   
pasword="ffffffffffff"

jKey="89b55ec0ef74a49a1908da08e43816508500abb6c4392396f7aacd55fd991dd3"
class ShoonyaApiPy(NorenApi):
    def __init__(self):
#        super().__init__(host='https://api.shoonya.com/NorenWClientAPI/', websocket='wss://api.shoonya.com/NorenWSAPI/')
        NorenApi.__init__(self, host='https://trade.shoonya.com/NorenWClientWeb/',websocket='wss://trade.shoonya.com/NorenWSWeb/')
api = ShoonyaApiPy()

result = api.set_session(
    user_id,
    pasword,
    jKey
)
print("SESSION RESULT:", result)

ret = api.place_order(buy_or_sell='B', product_type='I',
                        exchange='NSE', tradingsymbol='IDEA-EQ', 
                        quantity=1, discloseqty=0,price_type='MKT', price=0.0, trigger_price=0,
                        retention='DAY')
print(f'order response : {ret}' )

CANCEL = api.cancel_order(26111700155372)
print(CANCEL)

print("\nCHECKING SHOONYA ORDER BOOK...")

try:
    orders = api.get_order_book()
    print("ORDER BOOK RESPONSE:")
    print(repr(orders))
except Exception as e:
    print("ORDER BOOK ERROR:", repr(e))


