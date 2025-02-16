import sqlite3

con = sqlite3.connect("aliexpress.db")

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
        CREATE TABLE IF NOT EXISTS posts(
        ProductId text UNIQUE, 
        post text
        )""")

#print(cur.execute(f"SELECT rowid FROM products ORDER BY rowid ASC").fetchall())
#print(cur.execute(f"SELECT rowid,* FROM posts").fetchall())

def insertpost(data):

    try:
        cur.execute("INSERT INTO posts (ProductId, post) VALUES(?,?) ", (data[0][0],data[1]))
        con.commit()
    except Exception as e:
        print(e)
        pass

def getpost(data):
    return cur.execute(f"SELECT * FROM posts WHERE ProductId = {data}").fetchone()

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