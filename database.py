import sqlite3

con = sqlite3.connect("aliexpress.db", check_same_thread=False)

cur = con.cursor()

cur.execute("""
            CREATE TABLE IF NOT EXISTS products(
            ProductId text UNIQUE, 
            ImageUrl text , 
            VideoUrl text, 
            ProductDesc text, 
            Price integer, 
            Feedback integer, 
            PromotionUrl text,
            Category text

            )""")

cur.execute("""
            CREATE TABLE IF NOT EXISTS post(
            PostId text UNIQUE, 
            ProductId text
            )""")

def poststable(leng):

    cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {leng}posts(
            ProductId text UNIQUE, 
            post text
            )""")

#print(cur.execute(f"SELECT rowid FROM products ORDER BY rowid ASC").fetchall())
#print(cur.execute(f"SELECT rowid,* FROM posts").fetchall())

def get_product_details_by_post(post_id):
    # שליפת ה-ProductId מהפוסט
    cur.execute("""
        SELECT ProductId FROM post WHERE PostId = ?
    """, (post_id,))
    product_id = cur.fetchone()

    if product_id:
        # שליפת פרטי המוצר לפי ה-ProductId
        product_id = product_id[0]
        cur.execute("""
            SELECT * FROM products WHERE ProductId = ?
        """, (product_id,))
        product_details = cur.fetchone()

        if product_details:
            return {
                'ImageUrl': product_details[1],
                'ProductDesc': product_details[3],
                'Feedback': product_details[5],
                'PromotionUrl': product_details[6],
            }
        else:
            return None
    else:
        return None


def saveposts(postId,productId):
        try:
            cur.execute("INSERT INTO post VALUES(?,?) ", (postId,productId))
            con.commit()
        except Exception as e:
            print(e)
            pass

def insertpost(data, leng):

    try:
        cur.execute(f"INSERT INTO {leng}posts (ProductId, post) VALUES(?,?) ", (data[0][0],data[1]))
        con.commit()
    except Exception as e:
        print(e)
        pass

def getpost(data,leng):

    poststable(leng)

    return cur.execute(f"SELECT * FROM {leng}posts WHERE ProductId = {data}").fetchone()

def isset(productid):
    query = "SELECT EXISTS(SELECT 1 FROM products WHERE ProductId = ?)" 
    cur.execute(query, (productid,))
    return cur.fetchone()[0]  # Returns 1 if exists, 0 if not

def insertdatatotable(data):
    try:
        cur.execute("INSERT INTO products VALUES(?,?,?,?,?,?,?,?) ", data)
        con.commit()
    except:
        pass

def selectrandom(data):

    if data:
        try:
            return cur.execute(f"SELECT * FROM products WHERE Category = ? ORDER BY RANDOM() LIMIT 1", (data,)).fetchone()
        except:
            pass
    else:
        try:
            return cur.execute(f"SELECT * FROM products ORDER BY RANDOM() LIMIT 1").fetchone()
        except:
            pass