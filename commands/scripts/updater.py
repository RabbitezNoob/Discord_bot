import aiosqlite,httpx,time,logging
from discord.ext import tasks
from sqlite3 import OperationalError

api_v3_link='https://api.politicsandwar.com/graphql?api_key=819fd85fdca0a686bfab'
tasks.add_exception_type = OperationalError

@tasks.loop(minutes=5,reconnect=True)
async def update_nation_data():
	start_time=time.time()
	request_url='https://politicsandwar.com/api/v2/nations/819fd85fdca0a686bfab/&min_score=20'
	async with httpx.AsyncClient() as client:
		nationdata= await client.get(request_url,timeout=20)
		nationdata=nationdata.json()
	list_of_values=[]
	for x in nationdata['data']:
		values =tuple(value if not isinstance(value,str) else f"{value}" for value in x.values())
		list_of_values.append(values)
	async with aiosqlite.connect('pnw.db',timeout=20.0) as db:
		await db.execute('delete from all_nations_data')
		await db.executemany(f'''INSERT INTO all_nations_data VALUES ({','.join(['?' for x in range(0,len(list_of_values[0]))])})''', list_of_values)   
		await db.commit()
	end_time=time.time()
	logging.info(f'Time nation={end_time-start_time}')
pass

@tasks.loop(minutes=2,reconnect=True)
async def update_loot_data():
	async with aiosqlite.connect('pnw.db',timeout=20.0) as db:
		start_time=time.time()
		query="""{wars(active:false,first:1000,status:INACTIVE){
					data{
						id,end_date,winner_id,att_id,def_id,war_type
						attacker{
							alliance_position,war_policy,advanced_pirate_economy
						}  
						defender{
							alliance_position,war_policy,advanced_pirate_economy
						}
					}
					}	}"""
		async with httpx.AsyncClient() as client:
			fetchdata=await client.post(api_v3_link,json={'query':query})
			fetchdata=fetchdata.json()['data']['wars']['data']
			for war in fetchdata:
				if war['winner_id'] != '0':
					loser_id= war['def_id'] if war['winner_id']==war['att_id'] else war['att_id']
					date=war['end_date'].split('+')[0]
					date_obj=time.strptime(date,"%Y-%m-%dT%H:%M:%S")
					search =('select war_end_date from loot_data where nation_id=%s') %(loser_id)
					cursor = await db.execute(search)
					compare_date=await cursor.fetchone()
					compare_date=time.strptime(compare_date[0],"%Y-%m-%dT%H:%M:%S") if compare_date!= None else compare_date
					if compare_date==None or compare_date<date_obj:
						war_query=f"""{{
							warattacks(war_id:{war['id']}){{
							data{{
								money_looted,food_looted,coal_looted,oil_looted,uranium_looted,lead_looted,iron_looted,bauxite_looted,gasoline_looted,munitions_looted,steel_looted,aluminum_looted
								}}	
							}}
							}}"""
						loot_data = await client.post(api_v3_link,json={'query':war_query})
						if 'Too Many Requests' in str(loot_data):
							break
						
						war_type = war['war_type']
						if war['winner_id']==war['att_id']:
							if war['defender']!=None:
								loser_aa_position = war['defender']['alliance_position']
								loser_war_policy = war['defender']['war_policy']
							else:
								loser_aa_position = "NOALLIANCE"
								loser_war_policy = None
							if war['attacker']!=None:		
								winner_war_policy = war['attacker']['war_policy']
								winner_ape = war['attacker']['advanced_pirate_economy']
							else:
								winner_war_policy = None
								winner_ape = False	
							attrition_def_win = False
						else:
							if war['attacker']!=None:
								loser_aa_position = war['attacker']['alliance_position']
								loser_war_policy = war['attacker']['war_policy']
							else:
								loser_aa_position = "NOALLIANCE"
								loser_war_policy = None
							if war['defender']!=None:		
								winner_war_policy = war['defender']['war_policy']
								winner_ape = war['defender']['advanced_pirate_economy']
							else:
								winner_war_policy = None
								winner_ape = False		
							attrition_def_win = True 	

						policy_modifier = 1
						if winner_war_policy == "ATTRITION":
							policy_modifier -= 0.2
						elif winner_war_policy == "PIRATE":
							policy_modifier += 0.4

						if loser_war_policy == "TURTLE":
							policy_modifier +=0.2
						elif loser_war_policy == "MONEYBAGS":
							policy_modifier -= 0.4
						elif loser_war_policy == "GUARDIAN":
							policy_modifier += 0.2

						type_modifier = 1
						if war_type == "ORDINARY":
							type_modifier = 0.5
						elif war_type == "RAID":
							type_modifier = 1
						else:	
							if attrition_def_win:
								type_modifier = 0.5
							else:
								type_modifier = 0.25

						ape_modifier = 1.1 if winner_ape else 1

						loot_modifier = policy_modifier*type_modifier*ape_modifier*10			


						loot_data=loot_data.json()['data']['warattacks']['data']
						loot_data = loot_data[1] if loser_aa_position not in ["NOALLIANCE","APPLICANT"] else loot_data[0]

						for loot_type in loot_data:
							loot_data[loot_type] = int((loot_data[loot_type]*100/loot_modifier)-loot_data[loot_type])

						loot=','.join(str(x) for x in loot_data.values())
						loot_with_text=','.join([f"{key.split('_')[0]}={value}" if 'lead' not in key else f"`{key.split('_')[0]}`={value}" for key,value in loot_data.items() ])
						data_to_be_inserted=(f"insert into loot_data values({war['id']},{loser_id},{loot},'{date}') on conflict(nation_id) do update set war_id={war['id']},{loot_with_text},war_end_date='{date}'")
						await db.execute(data_to_be_inserted)
		await cursor.close()
		await db.commit()
	end_time=time.time()
	logging.info(f'Time loot={end_time-start_time}')
pass		

@tasks.loop(hours=2,reconnect=True)
async def update_trade_price():
	start_time=time.time()
	query="""{
			tradeprices(first:1){data{
				food,coal,oil,uranium,lead,iron,bauxite,gasoline,munitions,steel,aluminum
  			} }
  			}"""
	async with httpx.AsyncClient() as client:
		fetchdata=await client.post(api_v3_link,json={'query':query})
		fetchdata=fetchdata.json()['data']['tradeprices']['data'][0]
		logging.info(fetchdata)
	insert_text=','.join([f"{price}" for price in fetchdata.values()])
	sql_insert_to_table=f"insert into trade_prices values ({insert_text})"
	logging.info(sql_insert_to_table)
	async with aiosqlite.connect('pnw.db',timeout=20.0) as db:
		await db.execute("delete from trade_prices")
		await db.execute(sql_insert_to_table)
		await db.commit()
	end_time=time.time()
	logging.info(f'Time trade={end_time-start_time}')
pass