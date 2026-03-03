from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from web3 import Web3
from pymongo import MongoClient
from bson import ObjectId
from dotenv import load_dotenv
import os
import json
from flask import request, jsonify
from datetime import datetime

from flask_mail import Mail, Message


from time import time, sleep

load_dotenv()
processing_pickups = set()

app = Flask(__name__)




app.config['SECRET_KEY'] = os.environ.get("SECRET_KEY")

 # Keep this secret in production!
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.getenv("MAIL_USERNAME")
app.config['MAIL_PASSWORD'] = os.getenv("MAIL_PASSWORD")
app.config['MAIL_DEFAULT_SENDER'] = os.getenv("MAIL_USERNAME")

mail = Mail(app)




# ------------------- BLOCKCHAIN CONNECTION -------------------
w3 = Web3(Web3.HTTPProvider(os.getenv("RPC_URL")))
master_private_key = os.getenv("MASTER_PRIVATE_KEY")

MASTER_WALLET_ADDRESS = os.getenv("MASTER_WALLET_ADDRESS")
MASTER_PRIVATE_KEY = os.getenv("MASTER_PRIVATE_KEY")

if not MASTER_WALLET_ADDRESS or not MASTER_PRIVATE_KEY:
    raise RuntimeError("❌ MASTER wallet env variables missing")


# ✅ Load ABI from compiled_code.json
with open('compiled_code.json') as f:
    compiled_contract = json.load(f)

if "abi" in compiled_contract:
    abi = compiled_contract["abi"]
elif "contracts" in compiled_contract:
    first_file = list(compiled_contract["contracts"].keys())[0]
    first_contract = list(compiled_contract["contracts"][first_file].keys())[0]
    abi = compiled_contract["contracts"][first_file][first_contract]["abi"]
else:
    raise KeyError("ABI not found in compiled_code.json")
# ------------------- LOAD CONTRACT -------------------
contract_address = Web3.to_checksum_address(os.getenv("CONTRACT_ADDRESS"))
contract = w3.eth.contract(address=contract_address, abi=abi)


# ------------------- DATABASE CONNECTION -------------------
mongo_uri = os.getenv("MONGO_URI")
db_name = os.getenv("DB_NAME")

client = MongoClient(mongo_uri)
db = client[db_name]

farmers_collection = db["farmers"]
customers_collection = db["customers"]
cereals_collection = db["cereals"]
couriers_collection = db["couriers"]
orders_collection = db["orders"]
cart_collection = db["user_cart"]
couriers_collection = db["couriers"]
deliveries_collection = db["deliveries"]
support_collection = db["support_requests"]



# cereals = list(cereals_collection.find())
# for idx, cereal in enumerate(cereals, start=1):
#     cereals_collection.update_one(
#         {"_id": cereal["_id"]},
#         {"$set": {"blockchain_id": idx}}
#     )



# ------------------- FUND NEW WALLET -------------------
def fund_new_wallet(new_wallet_address):
    try:
        tx = {
            'from': MASTER_WALLET_ADDRESS,
            'to': new_wallet_address,
            'value': w3.to_wei(0.05, 'ether'),
            'gas': 21000,
            'gasPrice': w3.to_wei('10', 'gwei'),
            'nonce': w3.eth.get_transaction_count(MASTER_WALLET_ADDRESS),
        }
        signed_tx = w3.eth.account.sign_transaction(tx, MASTER_PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        print(f"✅ Sent 0.005 ETH to {new_wallet_address}")
        print("🔗 Tx Hash:", w3.to_hex(tx_hash))
        return True
    except Exception as e:
        import traceback
        print("❌ Blockchain error during funding:", traceback.format_exc())
        return False


# ------------------- ENSURE WALLET GAS -------------------
def ensure_wallet_has_gas(wallet_address):
    try:
        balance = w3.eth.get_balance(wallet_address)
        min_required = w3.to_wei(0.005, 'ether')
        if balance <= min_required:
            print(f"⛽ Low gas detected for {wallet_address}. Refunding...")
            fund_new_wallet(wallet_address)
        else:
            print(f"✅ {wallet_address} has sufficient gas balance.")
    except Exception as e:
        print(f"⚠ Error checking/funding wallet {wallet_address}: {e}")

def send_courier_email(courier_email, product_name, quantity, customer_address):
    try:
        msg = Message(
            subject="New Delivery Assigned - Farm to Fork",
            recipients=[courier_email]
        )

        msg.body = f"""
Hello Courier,

A new delivery has been assigned to you.

Product : {product_name}
Quantity: {quantity} kg
Customer Address: {customer_address}

Please log in to your courier dashboard for more details.

Regards,
Farm to Fork Team
"""
        mail.send(msg)
        print("Email sent successfully!")
    except Exception as e:
        print("Email sending failed:", str(e))



@app.route("/sign_order", methods=["POST"])
def sign_order():
    try:
        data = request.get_json(force=True)
    except:
        return jsonify({"error": "Invalid JSON"}), 400

    product_id = data.get("product_id")
    order_id = data.get("order_id")
    role = data.get("role")
    signature = data.get("signature")  # optional, can use wallet address

    if role not in ["courier", "customer"]:
        return jsonify({"error": "Invalid role"}), 400

    product = cereals_collection.find_one({"blockchain_id": product_id})
    if not product:
        return jsonify({"error": "Product not found"}), 404

    orders = product.get("orders", [])
    order = next((o for o in orders if o["orderId"] == order_id), None)
    if not order:
        return jsonify({"error": "Order not found"}), 404

    if order["signatures"].get(role):
        return jsonify({"error": f"{role.capitalize()} already signed"}), 400

    if role == "courier":
        wallet = session.get("courier_wallet")
        private_key = session.get("courier_private_key")
    else:
        wallet = session.get("customer_wallet")
        private_key = session.get("customer_private_key")

    if not wallet or not private_key:
        return jsonify({"error": f"{role.capitalize()} wallet info missing"}), 400

    try:
        contract_address = os.getenv("CONTRACT_ADDRESS")
        contract_address = Web3.to_checksum_address(contract_address)
        contract = w3.eth.contract(address=contract_address, abi=abi)
        nonce = w3.eth.get_transaction_count(wallet, "pending")

        txn = contract.functions.signCertificate(
            product_id,
            role,
            wallet
        ).build_transaction({
            'chainId': 11155111,
            'gas': 200000,
            'gasPrice': w3.to_wei('10', 'gwei'),
            'nonce': nonce,
            'from': wallet
        })

        signed_txn = w3.eth.account.sign_transaction(txn, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed_txn.raw_transaction)
        tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        if not tx_receipt or tx_receipt.status != 1:
            return jsonify({"error": "Blockchain transaction failed"}), 500

        cereals_collection.update_one(
            {"blockchain_id": product_id, "orders.orderId": order_id},
            {"$set": {f"orders.$.signatures.{role}": wallet}}
        )

        updated_order = cereals_collection.find_one(
            {"blockchain_id": product_id, "orders.orderId": order_id},
            {"orders.$": 1}
        )["orders"][0]

        if all(updated_order["signatures"].values()):
            cereals_collection.update_one(
                {"blockchain_id": product_id, "orders.orderId": order_id},
                {"$set": {"orders.$.status": "verified"}}
            )

        return jsonify({
            "message": f"{role.capitalize()} signature added successfully",
            "tx_hash": w3.to_hex(tx_hash),
            "order_status": updated_order["status"]
        })

    except Exception as e:
        print("❌ Blockchain signing error:", e)
        return jsonify({"error": "Blockchain signing failed", "details": str(e)}), 500


# ------------------- FARMER SIGNUP -------------------
@app.route("/signup_farmer", methods=["GET", "POST"])
def signup_farmer():
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"]
        password = request.form["password"]
        phone = request.form.get("phone")

        if farmers_collection.find_one({"email": email}):
            flash("Farmer already registered!", "warning")
            return redirect(url_for("signup_farmer"))

        account = w3.eth.account.create()
        wallet_address = account.address
        private_key = account._private_key.hex()
        fund_new_wallet(wallet_address)

        farmers_collection.insert_one({
            "name": name,
            "email": email,
            "password": password,
            "wallet_address": wallet_address,
            "private_key": private_key,
            "phone": phone,
            "verified": True
        })

        flash("Signup successful! You can login now.", "success")
        return redirect(url_for("login_farmer"))

    # ✅ IMPORTANT: render page on GET
    return render_template("signup_farmer.html")


    


# ------------------- CUSTOMER SIGNUP -------------------
@app.route("/signup_customer", methods=["GET", "POST"])
def signup_customer():
    if request.method == "POST":
        try:
            name = request.form.get("name")
            email = request.form.get("email")
            password = request.form.get("password")
            phone = request.form.get("phone")

            if not name or not email or not password or not phone:
                flash("All fields are required!", "danger")
                return redirect(url_for("signup_customer"))

            if customers_collection.find_one({"email": email}):
                flash("Customer already registered!", "warning")
                return redirect(url_for("signup_customer"))

            account = w3.eth.account.create()
            wallet_address = account.address
            private_key = account._private_key.hex()

            print("Generated Account:", wallet_address)

            fund_new_wallet(wallet_address)

            customers_collection.insert_one({
                "name": name,
                "email": email,
                "password": password,
                "phone": phone,
                "wallet_address": wallet_address,
                "private_key": private_key,
                "verified": True
            })

            flash("Signup successful! You can login now.", "success")
            return redirect(url_for("login_customer"))


        except Exception as e:
            print("SIGNUP ERROR:", e)
            flash("Signup failed. Internal error.", "danger")
            return redirect(url_for("signup_customer"))

    return render_template("signup_customer.html")


# ------------------- FARMER LOGIN -------------------
@app.route("/login_farmer", methods=["GET", "POST"])
def login_farmer():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        farmer = farmers_collection.find_one({"email": email, "password": password})

        if not farmer:
            flash("Invalid credentials", "danger")
            return redirect(url_for("login_farmer"))


        session["farmer_email"] = farmer["email"]
        session["farmer_wallet"] = farmer["wallet_address"]
        session["farmer_name"] = farmer["name"]

        return redirect(url_for("farmer_profile"))

    return render_template("login_farmer.html")


# ------------------- CUSTOMER LOGIN -------------------
@app.route("/login_customer", methods=["GET", "POST"])
def login_customer():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        customer = customers_collection.find_one({"email": email, "password": password})

        if not customer:
            flash("Invalid email or password", "danger")
            return redirect(url_for("login_customer"))

        
        session["customer_email"] = customer["email"]
        session["customer_wallet"] = customer["wallet_address"]
        session["customer_private_key"] = customer["private_key"]   # <-- REQUIRED
        session["customer_name"] = customer["name"]

        return redirect(url_for("customer_home"))

    return render_template("login_customer.html")


# ------------------- SEND VERIFICATION EMAIL -------------------
# ------------------- SEND VERIFICATION LINK -------------------


# ------------------- FARMER PROFILE -------------------
@app.route("/farmer_profile")
def farmer_profile():
    if "farmer_email" not in session:
        flash("Please login first", "warning")
        return redirect(url_for("login_farmer"))

    farmer_email = session["farmer_email"]
    farmer = farmers_collection.find_one({"email": farmer_email})

    cereals_data = cereals_collection.find({"farmer_email": farmer_email})
    
    cereals = []
    for c in cereals_data:
        # ⭐ FIX: read ratings from orders
        orders = c.get("orders", [])
        print("ORDERS DEBUG:", orders)
        ratings = [
        int(o["rating"])
        for o in orders
        if "rating" in o and isinstance(o["rating"], int)
        ]


        if ratings:
            avg_rating = round(sum(ratings) / len(ratings), 1)
        else:
            avg_rating = "No rating"

        c["avg_rating"] = avg_rating
        cereals.append(c)
   

    needs_address = False
    if not farmer.get("address"):
        needs_address = True
        flash("⚠ Please add your farm address before posting cereals.", "warning")

    return render_template(
        "farmer_profile.html",
        farmer=farmer,
        cereals=cereals,
        needs_address=needs_address
    )




# ------------------- CUSTOMER HOME -------------------
@app.route("/customer_home")
def customer_home():
    if "customer_email" not in session:
        flash("Please login first", "warning")
        return redirect(url_for("login_customer"))

    customer_email = session["customer_email"]
    customer = customers_collection.find_one({"email": customer_email})

    cereals = cereals_collection.find()
    products = []

    for c in cereals:
        # ---------- Rating compute ----------
        order_ratings = [order.get("rating") for order in c.get("orders", []) if order.get("rating")]
        if order_ratings:
            avg_rating =  round(sum(order_ratings) / len(order_ratings), 1)
        else:
            avg_rating = 0

        product_info = {
            "_id": str(c["_id"]),
            "blockchain_id": c.get("blockchain_id"),
            "name": c["name"],
            "price": c["price"],
            "availableQuantity": c.get("availableQuantity", 0),
            "description": c.get("description", ""),
            "farmer_name": c.get("farmer_name", "Unknown"),
            "farmer_address": c.get("farmer_wallet", "N/A"),
            "avg_rating": avg_rating,     # ⭐⭐⭐ ADDED
            "orders": []
        }

        # Orders of this customer
        for order in c.get("orders", []):
            if order.get("customer_email") == customer_email:
                product_info["orders"].append({
                    "orderId": order.get("orderId"),
                    "quantity": order.get("quantity", 0),
                    "status": order.get("status", "pending"),
                    "signatures": order.get("signatures", {})
                })

        products.append(product_info)

    # -------- Sort by rating (Best first) --------
    products.sort(key=lambda x: x.get("avg_rating", 0), reverse=True)

    return render_template("customer_home.html", products=products, customer=customer)




@app.route("/update_customer_address", methods=["POST"])
def update_customer_address():
    if "customer_email" not in session:
        flash("Please login first", "warning")
        return redirect(url_for("login_customer"))

    address = request.form.get("address", "").strip()
    map_link = request.form.get("map_link", "").strip()

    if not address:
        flash("Address cannot be empty", "warning")
        return redirect(url_for("customer_home"))

    customers_collection.update_one(
        {"email": session["customer_email"]},
        {"$set": {"address": address, "map_link": map_link, "has_address": True}}
    )

    flash("✅ Address updated successfully!", "success")
    return redirect(url_for("customer_home"))
# ------------------- ADD TO CART -------------------
from flask import Flask, request, session, redirect, url_for, flash
from bson import ObjectId

@app.route("/add_to_cart", methods=["POST"])
def add_to_cart():
    if "customer_email" not in session:
        flash("Please login first!", "warning")
        return redirect(url_for("login_customer"))

    customer_email = session["customer_email"]

    customer = customers_collection.find_one({"email": customer_email})
    address = customer.get("address") if customer else None
    if not address:
        flash("Please add your delivery address first.", "warning")
        return redirect(url_for("customer_home"))

    product_id = request.form.get("product_id")
    blockchain_id = request.form.get("blockchain_id")

    if not product_id:
        flash("Invalid product!", "danger")
        return redirect(url_for("customer_home"))

    product = cereals_collection.find_one({"_id": ObjectId(product_id)})
    if not product:
        flash("Product not found!", "danger")
        return redirect(url_for("customer_home"))

    if int(product.get("availableQuantity", 0)) <= 0:
        flash("Out of stock!", "danger")
        return redirect(url_for("customer_home"))

    user_cart = cart_collection.find_one({"email": customer_email}) or {
        "email": customer_email, "cart": []
    }

    cart = user_cart["cart"]

    for item in cart:
        if item["product_id"] == product_id:
            item["quantity"] += 1
            item["total"] = item["quantity"] * item["price"]
            break
    else:
        cart.append({
            "product_id": product_id,
            "blockchain_id": int(blockchain_id) if blockchain_id else None,
            "name": product["name"],
            "price": int(product["price"]),
            "quantity": 1,
            "total": int(product["price"]),
            "farmer_name": product.get("farmer_name", "Unknown"),
            "farmer_wallet": product.get("farmer_wallet")
            })

    cart_collection.update_one(
        {"email": customer_email},
        {"$set": {"cart": cart}},
        upsert=True
    )

    flash("Product added to cart!", "success")
    return redirect(url_for("customer_home"))


from flask import request, redirect, url_for, session, flash

@app.route("/update_cart_quantity", methods=["POST"])
def update_cart_quantity():
    if "customer_email" not in session:
        flash("Please login first!", "warning")
        return redirect(url_for("login_customer"))

    customer_email = session["customer_email"]
    product_id = request.form.get("product_id")
    action = request.form.get("action")

    if not product_id or not action:
        flash("Invalid request!", "danger")
        return redirect(url_for("cart"))

    # Fetch product from cereals DB
    product = cereals_collection.find_one({"_id": ObjectId(product_id)})
    if not product:
        flash("Product not found!", "danger")
        return redirect(url_for("cart"))

    available_qty = int(product.get("availableQuantity", 0))

    # Fetch user cart from MongoDB
    user_cart = cart_collection.find_one({"email": customer_email})
    if not user_cart:
        return redirect(url_for("cart"))

    cart = user_cart.get("cart", [])

    for item in cart:
        if item["product_id"] == product_id:

            # 🔼 Increase quantity
            if action == "increase":
                if int(item["quantity"]) < available_qty:
                    item["quantity"] += 1
                else:
                    flash(f"Only {available_qty} kg available!", "warning")

            # 🔽 Decrease quantity
            elif action == "decrease":
                if item["quantity"] > 1:
                    item["quantity"] -= 1

            # Update total
            item["total"] = item["quantity"] * item["price"]
            break

    # Save back to MongoDB
    cart_collection.update_one(
        {"email": customer_email},
        {"$set": {"cart": cart}}
    )

    return redirect(url_for("cart"))


@app.route("/cart")
def cart():
    if "customer_email" not in session:
        flash("Please login first", "warning")
        return redirect(url_for("login_customer"))

    customer_email = session["customer_email"]
    user_cart = cart_collection.find_one({"email": customer_email})
    cart_items = user_cart.get("cart", []) if user_cart else []

    blockchain_cart = []

    for item in cart_items:
        blockchain_id = item.get("blockchain_id")

        try:
            # 🔗 BLOCKCHAIN DATA (PRIMARY)
            product = contract.functions.getProduct(int(blockchain_id)).call()

            blockchain_cart.append({
                "product_id": item["product_id"],
                "id": int(product[0]),
                "name": product[1],
                "origin": product[2],
                "is_delivered": product[3],
                "price": int(product[4]),
                "farmer_name": product[5],
                "farmer_wallet": product[6],
                "farmer_signature": product[7],
                "courier_signature": product[8],
                "customer_signature": product[9],
                "quantity": int(item["quantity"]),
                "total": int(product[4]) * int(item["quantity"])
            })

        except Exception as e:
            # 🧯 FALLBACK — NEVER DROP CART ITEM
            print("Blockchain read failed:", e)

            blockchain_cart.append({
                "product_id": item["product_id"],
                "id": None,
                "name": item["name"],
                "origin": "N/A",
                "is_delivered": False,
                "price": int(item["price"]),
                "farmer_name": item.get("farmer_name", "Unknown"),
                "farmer_wallet": item.get("farmer_wallet"),
                "farmer_signature": None,
                "courier_signature": None,
                "customer_signature": None,
                "quantity": int(item["quantity"]),
                "total": int(item["price"]) * int(item["quantity"]),
                "blockchain_error": True   # optional flag for UI
            })

    total_cost = sum(i["total"] for i in blockchain_cart)

    return render_template(
        "cart.html",
        cart_items=blockchain_cart,
        total_cost=total_cost
    )


@app.route("/remove_from_cart", methods=["POST"])
def remove_from_cart():
    if "customer_email" not in session:
        return redirect(url_for("login_customer"))

    customer_email = session["customer_email"]
    product_id = request.form.get("product_id")

    user_cart = cart_collection.find_one({"email": customer_email})
    cart = user_cart.get("cart", []) if user_cart else []

    new_cart = [item for item in cart if str(item["product_id"]) != str(product_id)]

    cart_collection.update_one(
        {"email": customer_email},
        {"$set": {"cart": new_cart}}
    )

    flash("Item removed from cart", "success")
    return redirect(url_for("cart"))


# ------------------- PAY FARMER -------------------
from flask import Flask, render_template, request, redirect, session, jsonify, url_for
from bson import ObjectId

@app.route('/pay_now', methods=['POST'])
def pay_now():
    if 'customer_email' not in session:
        flash("Please login first!", "warning")
        return redirect(url_for('login_customer'))

    customer_email = session['customer_email']

    user_cart = cart_collection.find_one({"email": customer_email})
    cart = user_cart.get("cart", []) if user_cart else []

    if not cart:
        flash("Cart is empty!", "danger")
        return redirect(url_for('cart'))

    customer = customers_collection.find_one({'email': customer_email})
    if not customer:
        flash("Customer not found", "danger")
        return redirect(url_for('cart'))

    # --- ENFORCE ADDRESS ---
    address = session.get("customer_address") or customer.get("address")
    map_link = session.get("customer_map") or customer.get("map_link")
    if not address:
        flash("Please add your delivery address before placing the order.", "warning")
        return redirect(url_for("customer_home"))

    customer_wallet = customer.get('wallet_address')
    customer_private_key = customer.get('private_key')

    if not customer_wallet or not customer_private_key:
        flash("Customer wallet info missing!", "danger")
        return redirect(url_for('cart'))

    payment_results = []
    order_items = []

    for item in cart:
        cereal_order_id = str(ObjectId()) 
        assigned_email = None             
        farmer = None                       
        farmer_email = None 
        mongo_id = item['product_id']

        cereal_doc = cereals_collection.find_one({"_id": ObjectId(mongo_id)})
        if not cereal_doc:
            payment_results.append({"item": "Unknown", "success": False, "error": f"Cereal not found: {mongo_id}"})
            continue

        name = cereal_doc.get("name", "Unknown")   
        available_qty = int(cereal_doc.get("availableQuantity", 0))
        if available_qty <= 0:
            flash(f"❌ {name} is OUT OF STOCK!", "danger")
            continue

        blockchain_id = int(cereal_doc["blockchain_id"])
        quantity = int(item['quantity'])
        farmer_email = cereal_doc.get("farmer_email")
        farmer = farmers_collection.find_one({"email": farmer_email}) if farmer_email else None

        # read product from contract
        price_wei = int(cereal_doc.get("price", 0)) 
        try:
            product = contract.functions.getProduct(blockchain_id).call()
            name = product[1]
        except Exception as e:
            price_wei = int(cereal_doc.get("price", 0))
            name = cereal_doc.get("name", "Unknown")
            print("Error reading product from blockchain:", e)

        ensure_wallet_has_gas(customer_wallet)

        

        try:
            nonce = w3.eth.get_transaction_count(customer_wallet, "pending")

            txn = contract.functions.payFarmer(blockchain_id).build_transaction({
                "from": customer_wallet,
                "value": price_wei,
                "nonce": nonce,
                "gas": 300000,
                "gasPrice": w3.to_wei(5, 'gwei')
            })

            signed_txn = w3.eth.account.sign_transaction(txn, private_key=customer_private_key)
            tx_hash = w3.eth.send_raw_transaction(signed_txn.raw_transaction)
            w3.eth.wait_for_transaction_receipt(tx_hash)

            # decrement availableQuantity
            cereals_collection.update_one(
                {"_id": ObjectId(mongo_id)},
                {"$inc": {"availableQuantity": -quantity}}
            )

            # assign courier
            couriers = list(couriers_collection.find({}, {"email": 1, "assigned_deliveries": 1, "name": 1}))
            if couriers:
                couriers_sorted = sorted(
                    couriers,
                    key=lambda c: len(c.get("assigned_deliveries") or [])
                )
                best_courier = couriers_sorted[0]
                assigned_email = best_courier["email"]

                couriers_collection.update_one(
    {"email": assigned_email},
    {"$addToSet": {"assigned_deliveries": int(blockchain_id)}}
)

                cereals_collection.update_one(
                    {"blockchain_id": blockchain_id},
                    {"$set": {"assigned_courier": assigned_email}}
                )

#                 # >>> ADDED FOR EMAIL NOTIFICATION <<<
#                 try:
#                     msg = Message(
#                         subject="New Delivery Assigned - Farm to Fork",
#                         sender=app.config['MAIL_USERNAME'],
#                         recipients=[assigned_email]
#                     )
#                     msg.body = f"""
# Hello {best_courier.get('name', 'Courier')},

# A new order has been assigned to you.

# Order Details:
# -----------------------
# Product: {name}
# Quantity: {quantity} kg
# Customer: {customer.get('name')}
# Address: {address}

# Please check your courier dashboard for tracking and updates.

# Regards,
# Farm to Fork Team
#                     """
#                     mail.send(msg)
#                 except Exception as e:
#                     print("Email sending failed:", e)

            
            cereals_collection.update_one(
                {"blockchain_id": blockchain_id},
                {"$push": {"orders": {
                    "orderId": cereal_order_id,
                    "customer_email": customer_email,
                    "quantity": quantity,
                    "status": "Paid",
                    "assigned_courier": assigned_email,
                    "signatures": {"farmer": product[7] if 'product' in locals() else None, "courier": None, "customer": None},
                    "tx_hash": tx_hash.hex() if 'tx_hash' in locals() else None
                }}}
            )

            payment_results.append({
                "item": name,
                "success": True,
                "txHash": tx_hash.hex() if 'tx_hash' in locals() else None,
                "assigned_courier": assigned_email
            })

        except Exception as e:
            payment_results.append({
                "item": name,
                "success": False,
                "error": str(e)
            })

        order_items.append({
            "product_id": mongo_id,
            "blockchain_id": blockchain_id,
            "name": name,
            "price": price_wei,
            "quantity": quantity,
            "total": price_wei * quantity,
            "assigned_courier": assigned_email,
            "customer_name": customer.get("name"),
            "customer_address": address,
            "customer_map": map_link,
            "farmer_name": farmer.get("name") if farmer else None,
            "farmer_address": farmer.get("address") if farmer else None,
            "cereal_order_id": cereal_order_id
        })

    # insert main order
    order_doc = {
        "customer_email": customer_email,
        "customer_name": customer.get("name"),
        "customer_address": address,
        "customer_map": map_link,
        "items": order_items,
        "results": payment_results,
        "timestamp": datetime.utcnow(),
        "status": "Paid"
    }
    result = orders_collection.insert_one(order_doc)

    # create deliveries
    for it in order_items:
        assigned_email = it.get("assigned_courier")
        courier_email = assigned_email  
        courier_doc = couriers_collection.find_one({"email": courier_email})
        courier_name = courier_doc.get("name") if courier_doc else "Unknown"

        deliveries_collection.insert_one({
    "order_id": result.inserted_id,
    "customer_email": customer_email,
    "product_id": int(it.get("blockchain_id")),
    "product_name": it.get("name"),
    "quantity": it.get("quantity"),
    "price": it.get("price"),
    "status": "Paid",

    "cereal_order_id": it.get("cereal_order_id"),

    # courier info
    "courier_name": courier_name,
    "courier_email": courier_email,

    # customer info
    "customer_address": address,
    "customer_map": map_link,

    # NEW ➜ farmer info
    # farmer info (FIXED)
    "farmer_name": it.get("farmer_name"),
    "farmer_address": it.get("farmer_address"),
    "farmer_email": farmer_email,
    "farmer_wallet": farmer.get("wallet_address") if farmer else None,
    "history": [{
        "stage": "Order Placed",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "location": "Order Created"
    }]
})

    # clear cart
    cart_collection.update_one(
        {"email": customer_email},
        {"$set": {"cart": []}}
    )

    flash("Payment successful! Order placed & courier assigned.", "success")
    return redirect(url_for("order_confirmation"))



@app.route("/order_confirmation")
def order_confirmation():
    if "customer_email" not in session:
        flash("Please login first", "warning")
        return redirect(url_for("login_customer"))

    last_order = orders_collection.find_one(
        {"customer_email": session["customer_email"]},
        sort=[("_id", -1)]
    )

    if not last_order:
        flash("No recent order found.", "warning")
        return redirect(url_for("customer_home"))

    enhanced_items = []

    for item in last_order.get("items", []):
        mongo_id = item["product_id"]

        # ✔ FIX: Always re-fetch blockchain_id from DB
        cereal_doc = cereals_collection.find_one({"_id": ObjectId(mongo_id)})
        blockchain_id = int(cereal_doc["blockchain_id"])

        try:
            product = contract.functions.getProduct(blockchain_id).call()

            enhanced_items.append({
    "name": product[1],
    "price": int(product[4]),
    "quantity": int(item["quantity"]),
    "product_id": blockchain_id ,   # ← Add this line!
    "blockchain_id": blockchain_id
})


        except Exception as e:
            print("Error loading order product:", e)

    last_order["items"] = enhanced_items
        # Create delivery record only if not already created
    # Calculate grand total in Python (Jinja cannot do accumulation)
    grand_total = 0
    for item in enhanced_items:
        grand_total += int(item["quantity"]) * int(item["price"])

    last_order["grand_total"] = grand_total

    existing_delivery = deliveries_collection.find_one({
    "order_id": last_order["_id"]
})


    if not existing_delivery:
        for item in last_order.get("items", []):
            deliveries_collection.insert_one({
                "order_id": str(last_order["_id"]),
                "customer_email": last_order["customer_email"],
                "product_id": item["product_id"],
                "product_name": item.get("name", "Unknown"),
                "quantity": item["quantity"],
                "price": item.get("price", 0),
                "status": "Paid",   # default status
                "courier_name": "Not Assigned",
                "courier_email": "N/A",
                "customer_address": last_order.get("customer_address", "Not provided"),
                "customer_map": last_order.get("customer_map", None),
                "history": [{
                    "stage": "Order Placed",
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }]
            })


    return render_template("order_confirmation.html", order=last_order)



    
# ------------------- POST CEREAL PAGE -------------------
# ------------------- POST CEREAL PAGE -------------------
# Show the form page
@app.route("/post_cereal")
def post_cereal():
    if "farmer_email" not in session:
        flash("Please login first", "warning")
        return redirect(url_for("login_farmer"))

    farmer = farmers_collection.find_one({"email": session["farmer_email"]})

    # Farmer MUST have address
    if not farmer.get("address"):
        flash("⚠ Please add your farm address before posting cereals.", "warning")
        return redirect(url_for("farmer_profile"))

    # ✔ SUCCESS: show post cereal page
    return render_template("post_cereal.html", farmer=farmer)





# Handle cereal saving (called via fetch from post_cereal.html)
@app.route("/save_cereal", methods=["POST"])
def save_cereal():
    print("💡 /save_cereal route called")
    try:
        data = request.get_json(force=True)
        print("📦 Received data:", data)
    except Exception:
        return jsonify({"error": "Invalid JSON format"}), 400

    name = data.get("name", "").strip()
    qty = data.get("qty")
    price = data.get("price")
    desc = data.get("desc", "").strip()

    if not name or not qty or not price:
        return jsonify({"error": "Missing required fields"}), 400

    try:
        qty = int(qty)
        price = int(price)
        if qty <= 0 or price <= 0:
            return jsonify({"error": "Price and quantity must be positive"}), 400
    except:
        return jsonify({"error": "Quantity and price must be numbers"}), 400

    farmer_email = session.get("farmer_email")
    if not farmer_email:
        return jsonify({"error": "Farmer not logged in"}), 401

    farmer = farmers_collection.find_one({"email": farmer_email})
    if not farmer:
        return jsonify({"error": "Farmer not found"}), 404

    # ✅ REQUIRED ADDRESS CHECK ADDED HERE
    if not farmer.get("address"):
        return jsonify({
            "error": "You must add your farm address in your profile before posting cereals."
        }), 400
    # -------------------------------------------------------------

    farmer_name = farmer.get("name")
    farmer_wallet = farmer.get("wallet_address")
    farmer_private_key = farmer.get("private_key")
    if not farmer_wallet or not farmer_private_key:
        return jsonify({"error": "Wallet info missing"}), 400

    # Ensure gas
    ensure_wallet_has_gas(farmer_wallet)

    # Generate product ID
    product_id = int(time())
    print(f"🆔 Generated blockchain product ID: {product_id}")

    try:
        # Blockchain transaction
        contract_address = os.getenv("CONTRACT_ADDRESS")
        contract_address = Web3.to_checksum_address(contract_address)
        contract = w3.eth.contract(address=contract_address, abi=abi)
        nonce = w3.eth.get_transaction_count(farmer_wallet, "pending")

        txn = contract.functions.createProduct(
            product_id,
            name,
            "India",
            int(price),
            farmer_name
        ).build_transaction({
            'chainId': 11155111,
            'gas': 500000,
            'gasPrice': w3.to_wei('10', 'gwei'),
            'nonce': nonce,
            'from': farmer_wallet
        })

        signed_txn = w3.eth.account.sign_transaction(txn, farmer_private_key)
        tx_hash = w3.eth.send_raw_transaction(signed_txn.raw_transaction)
        tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

        if not tx_receipt or tx_receipt.status != 1:
            return jsonify({"error": "Blockchain failed. Cereal not saved."}), 500

        # Farmer signature
        nonce += 1
        sign_txn = contract.functions.signCertificate(
            product_id,
            "farmer",
            farmer_wallet
        ).build_transaction({
            'chainId': 11155111,
            'gas': 200000,
            'gasPrice': w3.to_wei('10', 'gwei'),
            'nonce': nonce,
            'from': farmer_wallet
        })

        signed_sign_txn = w3.eth.account.sign_transaction(sign_txn, farmer_private_key)
        sign_tx_hash = w3.eth.send_raw_transaction(signed_sign_txn.raw_transaction)

        # Save to MongoDB
        cereal_doc = {
            "farmer_email": farmer_email,
            "name": name,
            "totalQuantity": qty,
            "availableQuantity": qty,
            "price": price,
            "description": desc,
            "farmer_name": farmer_name,
            "farmer_wallet": farmer_wallet,
            "blockchain_id": product_id,
            "tx_hash": w3.to_hex(tx_hash),
            "sign_tx_hash": w3.to_hex(sign_tx_hash),
            "signatures": {"farmer": farmer_wallet},
            "orders": [],
            "status": "available"
        }
        cereals_collection.insert_one(cereal_doc)

        return jsonify({
            "message": "Cereal saved to DB and blockchain successfully!",
            "tx_hash": w3.to_hex(tx_hash),
            "sign_tx_hash": w3.to_hex(sign_tx_hash),
            "blockchain_id": product_id
        })

    except Exception as e:
        print("❌ Blockchain error:", e)
        return jsonify({"error": "Blockchain transaction failed", "details": str(e)}), 500

@app.route("/update_farmer_address", methods=["POST"])
def update_farmer_address():
    if "farmer_email" not in session:
        flash("Please login first", "warning")
        return redirect(url_for("login_farmer"))

    address = request.form.get("address", "").strip()
    map_link = request.form.get("map_link", "").strip()

    if not address:
        flash("Address cannot be empty", "warning")
        return redirect(url_for("farmer_profile"))

    farmers_collection.update_one(
        {"email": session["farmer_email"]},
        {"$set": {"address": address, "map_link": map_link, "has_address": True}}
    )

    flash("✅ Address updated successfully!", "success")
    return redirect(url_for("farmer_profile"))


# ------------------- LOGOUT -------------------
@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully", "success")
    return redirect(url_for("index"))


@app.route("/verify_product", methods=["POST"])
def verify_product():
    try:
        data = request.get_json(force=True)
        product_id = int(data.get("product_id"))
    except Exception:
        return jsonify({"success": False, "error": "Invalid product ID"}), 400

    try:
        contract_address = os.getenv("CONTRACT_ADDRESS")
        contract_address = Web3.to_checksum_address(contract_address)
        contract = w3.eth.contract(address=contract_address, abi=abi)

        # Call getProduct
        product_data = contract.functions.getProduct(product_id).call()

        # Unpack returned values
        (
            _id,
            name,
            origin,
            isDelivered,
            price,
            farmerName,
            farmerWallet,
            farmerSignature,
            courierSignature,
            customerSignature
        ) = product_data

        # Convert 0x000...0 addresses to None
        ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
        courierSignature = None if courierSignature == ZERO_ADDRESS else courierSignature
        customerSignature = None if customerSignature == ZERO_ADDRESS else customerSignature

        # Decide status based on signatures:
        if courierSignature and not customerSignature:
            status = "Delivered (Awaiting Customer Signature)"
        elif courierSignature and customerSignature:
            status = "Verified"
        elif not courierSignature:
            status = "In Transit"
        else:
            status = "In Transit"


        return jsonify({
            "success": True,
            "name": name,
            "origin": origin,
            "isDelivered": isDelivered,
            "price": price,
            "farmerName": farmerName,
            "farmerWallet": farmerWallet,
            "signatures": {
                "farmer": farmerSignature,
                "courier": courierSignature,
                "customer": customerSignature
            },
            "status": status
        })

    except Exception as e:
        print("Verification error:", e)
        return jsonify({"success": False, "error": str(e)})
    

@app.route("/sign_customer", methods=["POST"])
def sign_customer():
    if "customer_email" not in session:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    try:
        data = request.get_json(force=True)
        product_id = int(data.get("product_id"))
        order_id = data.get("order_id")   # NEW
    except Exception:
        return jsonify({"success": False, "error": "Invalid data"}), 400

    try:
        # -------- BLOCKCHAIN VALIDATION --------
        contract_address = os.getenv("CONTRACT_ADDRESS")
        contract_address = Web3.to_checksum_address(contract_address)
        contract = w3.eth.contract(address=contract_address, abi=abi)

        product_data = contract.functions.getProduct(product_id).call()
        _, _, _, isDelivered, _, _, _, _, courierSig, customerSig = product_data

        ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

        # Must be delivered
        if not isDelivered:
            return jsonify({"success": False, "error": "Product not delivered yet"}), 400

        # Must have courier signature first
        if courierSig == ZERO_ADDRESS:
            return jsonify({"success": False, "error": "Courier has not signed yet"}), 400

        # BLOCKCHAIN signature allowed only once globally
        if customerSig != ZERO_ADDRESS:
            # Not an error → blockchain signed already
            pass

        # -------- SIGN ON BLOCKCHAIN (ONLY ONCE) --------
        if customerSig == ZERO_ADDRESS:
            customer_wallet = session.get("customer_wallet")
            private_key = session.get("customer_private_key")

            nonce = w3.eth.get_transaction_count(customer_wallet, "pending")
            tx = contract.functions.signCertificate(
                product_id,
                "customer",
                customer_wallet
            ).build_transaction({
                "from": customer_wallet,
                "nonce": nonce,
                "gas": 300000,
                "gasPrice": w3.to_wei('5', 'gwei')
            })

            signed_tx = w3.eth.account.sign_transaction(tx, private_key)
            tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            w3.eth.wait_for_transaction_receipt(tx_hash)
        else:
            tx_hash = "already_signed"

        # -------- SAVE SIGN IN DB FOR THAT SPECIFIC ORDER --------
        cereal = cereals_collection.find_one({"blockchain_id": product_id})
        orders = cereal.get("orders", [])

        for o in orders:
            if o.get("orderId") == order_id:
                o["customer_signed"] = True
                break

        cereals_collection.update_one(
            {"blockchain_id": product_id},
            {"$set": {"orders": orders}}
        )

        return jsonify({"success": True, "tx_hash": str(tx_hash)})

    except Exception as e:
        print("Customer sign error:", e)
        return jsonify({"success": False, "error": str(e)})



@app.route("/login_courier", methods=["GET", "POST"])
def login_courier():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        courier = couriers_collection.find_one({
            "email": email,
            "password": password
        })

        if not courier:
            flash("Invalid email or password", "danger")
            return redirect(url_for("login_courier"))

        session["courier_email"] = courier["email"]
        session["courier_wallet"] = courier["wallet_address"]
        session["courier_name"] = courier["name"]

        return redirect(url_for("courier_dashboard"))

    return render_template("login_courier.html")


@app.route("/signup_courier", methods=["GET", "POST"])
def signup_courier():
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"]
        password = request.form["password"]
        phone = request.form.get("phone")
        vehicle = request.form.get("vehicle")

        if couriers_collection.find_one({"email": email}):
            flash("Courier already exists!", "warning")
            return redirect(url_for("signup_courier"))

        account = w3.eth.account.create()
        wallet_address = account.address
        private_key = account._private_key.hex()
        fund_new_wallet(wallet_address)

        couriers_collection.insert_one({
            "name": name,
            "email": email,
            "password": password,
            "wallet_address": wallet_address,
            "private_key": private_key,
            "phone": phone,
            "vehicle": vehicle,
            "assigned_deliveries": [],
            "verified": True   # ✅ directly verified
        })

        flash("Signup successful! You can login now.", "success")
        return redirect(url_for("login_courier"))

    return render_template("signup_courier.html")


@app.route("/courier_dashboard")
def courier_dashboard():
    if "courier_email" not in session:
        flash("Please login first", "warning")
        return redirect(url_for("login_courier"))

    courier = couriers_collection.find_one({"email": session["courier_email"]})
    if not courier:
        flash("Courier not found", "danger")
        return redirect(url_for("login_courier"))

    # 🔹 Get all deliveries assigned to this courier
    delivery_docs = deliveries_collection.find({
    "courier_email": courier["email"],
    "status": {"$nin": ["Delivered", "Refunded", "Returned"]}
})
    
    deliveries = []

    for d in delivery_docs:
        try:
            # product_id in DB is blockchain id
            pid = int(d.get("product_id"))
        except Exception:
            # fallback if stored as string
            pid = int(d.get("product_id", 0))


        # --- Try to read fresh data from blockchain ---
        product_name = d.get("product_name", "Unknown")
        price = int(d.get("price", 0))
        farmer_name = d.get("farmer_name", "")
        farmer_wallet = None
        is_delivered = False

        try:
            product = contract.functions.getProduct(pid).call()
            # product = [id, name, origin, isDelivered, price, farmerName, farmerWallet, farmerSig, courierSig, customerSig]
            product_name = product[1]
            is_delivered = product[3]
            price = int(product[4])
            farmer_name = product[5]
            farmer_wallet = product[6]
        except Exception as e:
            print(f"Error reading product {pid} from blockchain:", e)

        status = "Delivered" if is_delivered else d.get("status", "In Transit")
 
        # --- Customer info from customers_collection ---
        customer_name = ""
        customer_address = d.get("customer_address", "")
        customer_phone = ""
        customer_map = d.get("customer_map", "")

        cust_email = d.get("customer_email")
        if cust_email:
            cust_doc = customers_collection.find_one({"email": cust_email})
            if cust_doc:
                customer_name = cust_doc.get("name", customer_name)
                customer_address = cust_doc.get("address", customer_address)
                customer_phone = cust_doc.get("phone", customer_phone)
                customer_map = cust_doc.get("map_link", customer_map)

        # --- Farmer info from farmers_collection (optional refine) ---
        farmer_address = d.get("farmer_address", "")
        farmer_map = None
        if farmer_wallet:
            farmer_doc = farmers_collection.find_one({"wallet_address": farmer_wallet})
        else:
            farmer_doc = None

        if farmer_doc:
            farmer_address = farmer_doc.get("address", farmer_address)
            farmer_map = farmer_doc.get("map_link", None)

        deliveries.append({
            "product_id": pid,
            "product_name": product_name,
            "price": price,
            "is_delivered": is_delivered,

            "order_id": str(d.get("order_id")),
            "quantity": d.get("quantity", 0),

            "status": status,
            "current_location": d.get("current_location", ""),
            "history": d.get("history", []),

            "customer_name": customer_name,
            "customer_address": customer_address,
            "customer_phone": customer_phone,
            "customer_map": customer_map,

            "farmer_name": farmer_name,
            "farmer_wallet": farmer_wallet,
            "farmer_address": farmer_address,
            "farmer_map": farmer_map,

            # for now, you can keep this None or later load from blockchain
            "courier_signature": product[8],
            "customer_signature": product[9],

            "stage": d.get("stage", 0),

        })

    return render_template("courier_dashboard.html",
                           courier=courier,
                           deliveries=deliveries)


# Assign courier to a product (called by farmer/admin)
# expects form or JSON: { "product_id": 12345, "courier_email": "ravi@..." }
@app.route("/assign_courier", methods=["POST"])
def assign_courier():
    data = request.get_json() if request.is_json else request.form
    product_id = int(data.get("product_id"))
    courier_email = data.get("courier_email")

    courier = couriers_collection.find_one({"email": courier_email})
    cereal = cereals_collection.find_one({"product_id": product_id})
    if not courier:
        return jsonify({"success": False, "error": "Courier not found"}), 404
    if not cereal:
        return jsonify({"success": False, "error": "Product not found in DB"}), 404

    # push product id to courier assigned_deliveries (avoid duplicates)
    couriers_collection.update_one(
        {"email": courier_email},
        {"$addToSet": {"assigned_deliveries": product_id}}
    )
    # set assigned courier in cereal DB doc
    cereals_collection.update_one(
        {"product_id": product_id},
        {"$set": {"assigned_courier": courier_email, "status": "Assigned"}}
    )

    return jsonify({"success": True, "message": "Courier assigned"})


# Courier updates delivery status / location
# Called via AJAX (JSON): { "product_id": 12345, "current_location": "On Route - Hub X", "stage": "Received from Farmer" }
from bson import ObjectId

from bson import ObjectId
from datetime import datetime

@app.route("/update_delivery", methods=["POST"])
def update_delivery():
    if "courier_email" not in session:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    data = request.get_json(force=True)
    product_id = int(data.get("product_id"))
    current_location = data.get("current_location", "")
    stage = data.get("stage")

    # Stage mapping
    stage_map = {
        "Picked up from Farmer": 1,
        "Received from Farmer": 2,
        "Shipped": 3,
        "Out for Delivery": 4,
        "Delivered": 5
    }
    next_stage = stage_map.get(stage)
    if next_stage is None:
        return jsonify({"success": False, "error": "Invalid stage"}), 400

    courier = couriers_collection.find_one({"email": session["courier_email"]})
    if not courier:
        return jsonify({"success": False, "error": "Courier not found"}), 404

    # Allow courier ONLY if:
    # 1. They were assigned, or
    # 2. The product is delivered and courier already signed
    assigned = [int(x) for x in courier.get("assigned_deliveries", [])]

    if product_id not in assigned:
    # Check blockchain courier signature
        _, _, _, _, _, _, _, courierSig, _ = contract.functions.getProduct(product_id).call()
        if courierSig == "0x0000000000000000000000000000000000000000":
            return jsonify({"success": False, "error": "Not assigned to this delivery"}), 403


    isDelivered = (stage == "Delivered")

    try:
        # ---------- Blockchain update (OWNER WALLET) ----------
        master_wallet = MASTER_WALLET_ADDRESS
        master_key = MASTER_PRIVATE_KEY

        if not master_wallet or not master_key:
            return jsonify({"success": False, "error": "Master wallet not configured"}), 500

        nonce = w3.eth.get_transaction_count(master_wallet, "pending")

        txn = contract.functions.updateProduct(
            product_id,
            current_location,
            isDelivered
        ).build_transaction({
            "chainId": 11155111,
            "gas": 300000,
            "gasPrice": w3.to_wei("10", "gwei"),
            "nonce": nonce,
            "from": master_wallet
        })

        signed = w3.eth.account.sign_transaction(txn, master_key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        print("Blockchain Delivery UPDATE MINED =", receipt.status)

        if receipt.status != 1:
            return jsonify({"success": False, "error": "Blockchain updateProduct failed"}), 500

        tx_hex = w3.to_hex(tx_hash)

        ts = datetime.now().strftime("%d %b %Y %I:%M:%S %p")

        # ---------- Deliveries DB ----------
        deliveries_collection.update_one(
            {"product_id": product_id, "courier_email": courier["email"]},
            {
                "$set": {
                    "status": stage,
                    "stage": next_stage,
                    "current_location": current_location
                },
                "$push": {
                    "history": {
                        "stage": stage,
                        "timestamp": ts,
                        "location": current_location
                    }
                }
            }
        )

        # ---------- Orders DB ----------
        orders_collection.update_one(
            {"items.blockchain_id": product_id},
            {
                "$set": {
                    "items.$.status": stage,
                    "items.$.current_location": current_location
                },
                "$push": {
                    "items.$.history": {
                        "stage": stage,
                        "timestamp": ts,
                        "location": current_location
                    }
                }
            }
        )

        # ---------- Cereals DB ----------
        cereals_collection.update_one(
            {"blockchain_id": product_id},
            {"$set": {"status": stage}}
        )

        # ---------- Safe removal of assignment ----------
        if stage == "Delivered":

            # Remove assignment from courier + cereal
            couriers_collection.update_one(
                {"email": courier["email"]},
                {"$pull": {"assigned_deliveries": int(product_id)}}
            )
            cereals_collection.update_one(
                {"blockchain_id": product_id},
                {"$unset": {"assigned_courier": ""}}
            )

            deliveries_collection.update_one(
                {"product_id": product_id, "courier_email": courier["email"]},
                {"$set": {"courier_completed": True}}
            )
            deliveries_collection.update_one(
                {"product_id": product_id, "courier_email": courier["email"]},
                {"$set": {"courier_email": None}}
            )

            # ---------- Courier signs on blockchain ----------
            try:
                courier_wallet = courier["wallet_address"]
                courier_key = courier["private_key"]

                nonce2 = w3.eth.get_transaction_count(courier_wallet, "pending")
                txn2 = contract.functions.signCertificate(
                    product_id,
                    "courier",
                    courier_wallet
                ).build_transaction({
                    "chainId": 11155111,
                    "gas": 250000,
                    "gasPrice": w3.to_wei("10", "gwei"),
                    "nonce": nonce2,
                    "from": courier_wallet
                })

                signed2 = w3.eth.account.sign_transaction(txn2, courier_key)
                tx_hash2 = w3.eth.send_raw_transaction(signed2.raw_transaction)
                w3.eth.wait_for_transaction_receipt(tx_hash2)

            except Exception as e:
                print("SignCertificate (delivery) failed:", e)

        return jsonify({"success": True, "tx_hash": tx_hex})

    except Exception as e:
        print("Error in update_delivery:", e)
        return jsonify({"success": False, "error": str(e)}), 500



@app.route("/courier_pickup", methods=["POST"])
def courier_pickup():
    if "courier_email" not in session:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    data = request.get_json()
    product_id = int(data.get("product_id"))

    # prevent duplicate processing
    if product_id in processing_pickups:
        return jsonify({"success": False, "error": "Pickup already processing"}), 429
    processing_pickups.add(product_id)

    courier = couriers_collection.find_one({"email": session["courier_email"]})
    if not courier:
        processing_pickups.discard(product_id)
        return jsonify({"success": False, "error": "Courier not found"}), 404

    # Ensure courier assigned
    assigned_list = [int(x) for x in courier.get("assigned_deliveries", [])]
    if int(product_id) not in assigned_list:
        processing_pickups.discard(product_id)
        return jsonify({"success": False, "error": "Not assigned"}), 403

    try:
        wallet = courier["wallet_address"]
        private_key = courier["private_key"]

        ensure_wallet_has_gas(wallet)

        # blockchain tx
        nonce = w3.eth.get_transaction_count(wallet, "pending")
        txn = contract.functions.signCertificate(
            product_id,
            "courier",
            wallet
        ).build_transaction({
            "from": wallet,
            "nonce": nonce,
            "gas": 250000,
            "gasPrice": w3.to_wei("5", "gwei")
        })

        signed_tx = w3.eth.account.sign_transaction(txn, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        w3.eth.wait_for_transaction_receipt(tx_hash)

        ts = datetime.now().strftime("%d %b %Y %I:%M:%S %p")

        # deliveries db update
        deliveries_collection.update_one(
            {"product_id": product_id, "courier_email": courier["email"]},
            {
                "$set": {
                    "courier_pickup_signed": True,
                    "stage": 1,
                    "status": "Picked up from Farmer"
                },
                "$push": {
                    "history": {
                        "stage": "Picked up from Farmer",
                        "timestamp": ts,
                        "location": "Picked from Farm"
                    }
                }
            }
        )

        # orders update
        orders_collection.update_one(
            {"items.blockchain_id": product_id},
            {
                "$set": {
                    "items.$.status": "Picked up from Farmer"
                },
                "$push": {
                    "items.$.history": {
                        "stage": "Picked up from Farmer",
                        "timestamp": ts,
                        "location": "Picked from Farm"
                    }
                }
            }
        )

        processing_pickups.discard(product_id)

        return jsonify({
            "success": True,
            "message": "Courier pickup confirmed",
            "tx_hash": tx_hash.hex()
        })

    except Exception as e:
        processing_pickups.discard(product_id)
        print("Courier pickup error:", e)
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# Courier logout
@app.route("/logout_courier")
def logout_courier():
    session.pop("courier_email", None)
    flash("Courier logged out", "success")
    return redirect(url_for("index"))

@app.route('/get_farmer_products')
def get_farmer_products():
    farmer_email = session.get('farmer_email')
    if not farmer_email:
        return jsonify({'products': []})
    products = list(cereals_collection.find({'farmer_email': farmer_email}, {'_id': 0, 'name': 1, 'blockchain_id': 1}))
    return jsonify({'products': products})


@app.route("/orders")
def orders():
    if "customer_email" not in session:
        flash("Please login first!", "warning")
        return redirect(url_for("login_customer"))

    customer_email = session["customer_email"]

    # Read all DB deliveries
    delivery_docs = list(deliveries_collection.find({"customer_email": customer_email}))

    result = []

    ZERO = "0x0000000000000000000000000000000000000000"

    for d in delivery_docs:

        try:
            pid = int(d["product_id"])
            product = contract.functions.getProduct(pid).call()

            courier_sig = product[8]
            customer_sig = product[9]

            courier_sig = None if courier_sig == ZERO else courier_sig
            customer_sig = None if customer_sig == ZERO else customer_sig

            d["courier_signature"] = courier_sig
            d["customer_signature"] = customer_sig

        except Exception as e:
            print("Signature fetch error:", e)
            d["courier_signature"] = None
            d["customer_signature"] = None

        # ---------------------------------------
        # ⭐ ADD RATING INFO PER ORDER
        # ---------------------------------------
        cereal = cereals_collection.find_one({"blockchain_id": pid})
        if cereal:
            for o in cereal.get("orders", []):
                if str(o.get("orderId")) == str(d.get("cereal_order_id")):
                    d["rating"] = o.get("rating")  # ⭐ add rating into response
                    break
            else:
                d["rating"] = None   # if not rated yet
        else:
            d["rating"] = None
        
        d["cereal_order_id"] = d.get("cereal_order_id")
        result.append(d)

    return render_template("orders.html", deliveries=result)



@app.route("/farmer_orders")
def farmer_orders():
    if "farmer_email" not in session:
        flash("Please login first!", "warning")
        return redirect(url_for("login_farmer"))

    farmer = farmers_collection.find_one(
        {"email": session["farmer_email"]}
    )
    if not farmer:
        flash("Farmer not found", "danger")
        return redirect(url_for("login_farmer"))

    farmer_wallet = farmer["wallet_address"]

    deliveries = list(
        deliveries_collection.find({"farmer_wallet": farmer_wallet})
    )

    return render_template("farmer_orders.html", deliveries=deliveries)


from flask_mail import Mail, Message

@app.route("/request_support", methods=["POST"])
def request_support():
    if "customer_email" not in session:
        return jsonify({"success": False, "error": "Login required"}), 401

    try:
        data = request.get_json(force=True)
        product_id = int(data.get("product_id"))
        courier_email = data.get("courier_email")
        if not courier_email or courier_email == "N/A":
            # do not store courier null
            courier_email = "N/A"
        req_type = data.get("type")
        reason = data.get("reason")
        customer_email = session["customer_email"]

        support_collection.insert_one({
            "product_id": product_id,
            "customer_email": customer_email,
            "courier_email": courier_email,
            "type": req_type,
            "reason": reason,
            "status": "Pending",
            "created_at": datetime.now()
        })

        # -------------------------------
        # SEND EMAIL TO COURIER
        # -------------------------------
        msg = Message(
            subject="Support Request Received",
            recipients=[courier_email],
            body=f"""
Hello Courier,

A new support request is submitted.

Product ID: {product_id}
Customer: {customer_email}
Type: {req_type}
Reason: {reason}

Please check your support dashboard.

Regards,
Farm2Fork System
"""
        )
        

        if courier_email == "N/A":
            print("No courier assigned, email skipped")
        else:
            mail.send(msg)

        return jsonify({"success": True})
    

    except Exception as e:
        print("Support request error:", e)
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/support_dashboard")
def support_dashboard():
    if "courier_email" not in session:
        return redirect(url_for("login_courier"))

    courier_email = session["courier_email"]

    requests = list(support_collection.find({"courier_email": courier_email}))

    return render_template("support_dashboard.html", requests=requests)


@app.route("/support_update", methods=["POST"])
def support_update():
    if "courier_email" not in session:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    data = request.get_json(force=True)
    req_id = data.get("request_id")
    update_to = data.get("status")

    req = support_collection.find_one({"_id": ObjectId(req_id)})
    if not req:
        return jsonify({"success": False, "error": "Request not found"}), 404

    request_type = req.get("type")   # return / cancel / other

    # ---------- COMMON DB UPDATE ----------
    support_collection.update_one(
        {"_id": ObjectId(req_id)},
        {"$set": {"status": update_to, "updated_at": datetime.now()}}
    )

    # ---------- SEND EMAIL TO CUSTOMER ----------
    customer_email = req["customer_email"]
    status_msg = ""

    if update_to == "Rejected":
        status_msg = "Your request has been rejected."
    elif update_to == "Approved":
        status_msg = "Your request has been approved. Refund/Return processing."
    elif update_to == "Pickup Pending":
        status_msg = "Pickup confirmed. Courier will collect item."
    elif update_to == "Picked Up":
        status_msg = "Item collected. Refund is being processed."

    msg = Message(
        subject="Support Update Notification",
        recipients=[customer_email],
        body=f"""
Hello Customer,

Your support request for Product ID: {req["product_id"]}

Status: {update_to}

{status_msg}

Regards,
Farm2Fork System
"""
    )
    mail.send(msg)


    # ------------------------------------------------------
    # CASE 1: REJECTED
    # ------------------------------------------------------
    if update_to == "Rejected":
        return jsonify({"success": True})

    # ------------------------------------------------------
    # CASE 2: OTHER ISSUE (NO REFUND)
    # ------------------------------------------------------
    if request_type == "other":
        return jsonify({"success": True, "msg": "Issue resolved"})

    # ------------------------------------------------------
    # CASE 3: CANCEL REQUEST (Immediate Refund)
    # ------------------------------------------------------
    if request_type == "cancel":
        if update_to == "Approved":
            return process_refund(req, req_id)
        return jsonify({"success": True})

    # ------------------------------------------------------
    # CASE 4: RETURN REQUEST (TWO STEPS)
    # ------------------------------------------------------

    # Step1: Courier approves pickup
    if update_to == "Pickup Pending":
        return jsonify({"success": True})

    # Step2: Courier picked item → Refund
    if update_to == "Picked Up":
        return process_refund(req, req_id)

    return jsonify({"success": True})


# -------------------------------------------------------------
# REFUND TRANSACTION PROCESSOR
# -------------------------------------------------------------
def process_refund(req, req_id):
    try:
        product_id = req["product_id"]

        product_data = contract.functions.getProduct(product_id).call()
        price = product_data[4]

        # Customer wallet
        customer = customers_collection.find_one({"email": req["customer_email"]})
        if not customer or "wallet_address" not in customer:
            return jsonify({"success": False, "error": "Customer wallet missing"}), 500

        customer_wallet = Web3.to_checksum_address(customer["wallet_address"])

        # Refund from master wallet
        master_wallet = Web3.to_checksum_address(MASTER_WALLET_ADDRESS)
        master_private_key = MASTER_PRIVATE_KEY

        tx = {
            "from": master_wallet,
            "to": customer_wallet,
            "value": price,
            "gas": 21000,
            "gasPrice": w3.to_wei("5", "gwei"),
            "nonce": w3.eth.get_transaction_count(master_wallet, "pending")
        }

        signed_tx = w3.eth.account.sign_transaction(tx, master_private_key)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)

        # Save tx hash
        support_collection.update_one(
            {"_id": ObjectId(req_id)},
            {"$set": {"refund_tx": w3.to_hex(tx_hash)}}
        )

        # 🔥 REMOVE from courier dashboard and DB
        deliveries_collection.update_many(
            {"product_id": product_id},
            {
                "$set": {
                    "status": "Refunded",
                    "courier_email": None,
                    "stage": -1
                }
            }
        )

        # 🔥 Remove from courier assignments
        couriers_collection.update_one(
            {"email": req["courier_email"]},
            {"$pull": {"assigned_deliveries": product_id}}
        )

        # 🔥 Also update cereal status
        cereals_collection.update_one(
            {"blockchain_id": product_id},
            {
                "$unset": {"assigned_courier": ""},
                "$set": {"status": "Refunded"}
            }
        )

        return jsonify({"success": True})

    except Exception as e:
        print("Refund error:", e)
        return jsonify({"success": False, "error": str(e)}), 500



@app.route("/customer_support_status")
def customer_support_status():
    if "customer_email" not in session:
        flash("Please login first!", "warning")
        return redirect(url_for("login_customer"))

    customer = session["customer_email"]

    reqs = list(support_collection.find(
        {"customer_email": customer},
        {"_id": 1, "product_id": 1, "type": 1, "reason": 1,
         "status": 1, "created_at": 1, "updated_at": 1, "refund_tx": 1}
    ))

    return render_template("customer_support_status.html", reqs=reqs)

@app.route("/submit_rating", methods=["POST"])
def submit_rating():
    if "customer_email" not in session:
        return jsonify({"success": False}), 401

    data = request.get_json(force=True)
    pid = int(data.get("product_id"))
    oid = data.get("order_id")   # <- IMPORTANT
    stars = int(data.get("stars"))

    cereal = cereals_collection.find_one({"blockchain_id": pid})
    if not cereal:
        return jsonify({"success": False, "error": "Invalid product"}), 404

    orders = cereal.get("orders", [])
    found = False

    for o in orders:
        if str(o.get("orderId")) == str(oid):     # FIXED
            o["rating"] = stars
            found = True
            break

    if not found:
        return jsonify({"success": False, "error": "Order not found"}), 404

    cereals_collection.update_one(
        {"blockchain_id": pid},
        {"$set": {"orders": orders}}
    )

    return jsonify({"success": True})


# ------------------- HOME -------------------
@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)


# ✅ (Rest of your code unchanged)
# ------------------- FARMER SIGNUP -------------------
# ... same as before ...
# ------------------- CUSTOMER SIGNUP -------------------
# ... same as before ...
# ------------------- OTHER ROUTES -------------------
# ... same as before ...



