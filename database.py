import sqlite3

con = sqlite3.connect("aliexpress.db", check_same_thread=False)

cur = con.cursor()

cur.execute("""
            CREATE TABLE IF NOT EXISTS products(
            ProductId text UNIQUE, 
            ImageUrl text , 
            ProductDesc text, 
            OriginPrice integer,
            DiscountPrice integer,
            Discount integer,
            Sales180Day integer,
            Feedback integer, 
            PromotionUrl text,
            Category text

            )""")

def poststable(leng):

    cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {leng}posts(
            ProductId text UNIQUE, 
            post text
            )""")

def getpost(data,leng):

    poststable(leng)

    return cur.execute(f"SELECT Post FROM {leng}posts WHERE ProductId = {data}").fetchone()

def isset(productid):
    query = "SELECT EXISTS(SELECT 1 FROM products WHERE ProductId = ?)" 
    cur.execute(query, (productid,))
    return cur.fetchone()[0]  # Returns 1 if exists, 0 if not


def selectrandom(data):

    if data:
        try:
            post = cur.execute(f"SELECT * FROM products WHERE Category = ? ORDER BY RANDOM() LIMIT 1", (data,)).fetchone()
            return {
                'ProductId': post[0],
                'ImageUrl': post[1],
                'ProductDesc': post[2],
                'OriginPrice': post[3],
                'DiscountPrice': post[4],
                'Discount': post[5],
                'Sales180Day': post[6],
                'Feedback': post[7],
                'PromotionUrl': post[8],
                'Category': post[9]
            }
        except:
            pass
    else:
        try:
            post = cur.execute("SELECT * FROM products ORDER BY RANDOM() LIMIT 1").fetchone()
            return {
                'ProductId': post[0],
                'ImageUrl': post[1],
                'ProductDesc': post[2],
                'OriginPrice': post[3],
                'DiscountPrice': post[4],
                'Discount': post[5],
                'Sales180Day': post[6],
                'Feedback': post[7],
                'PromotionUrl': post[8],
                'Category': post[9]
            }
        except:
            pass

def insertpost(data, leng):

    try:
        cur.execute(f"INSERT INTO {leng}posts (ProductId, post) VALUES(?,?) ", (data['ProductId'],data['post']))
        con.commit()
    except Exception as e:
        print(e)
        pass

def insertdatatotable(data):
    try:
        cur.execute("INSERT INTO products VALUES(?,?,?,?,?,?,?,?,?,?) ", data)
        con.commit()
    except:
        pass
