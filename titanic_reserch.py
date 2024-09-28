import csv
import pandas as pd
import matplotlib.pyplot as plt

with open("train.csv", "r", encoding="utf-8") as csv_file:
    df = pd.read_csv('train.csv')

row_count = len(df)
male_data = df[df['Sex'] == 'male'].shape[0]
female_data = df[df['Sex'] == 'female'].shape[0]

survival_counts_male = df[df['Sex'] == 'male']['Survived'].value_counts()
survival_counts_female = df[df['Sex'] == 'female']['Survived'].value_counts()
labels = ['Not Survived', 'Survived']


plt.figure(figsize=(10, 6))
survival_counts_male.plot(kind='bar')
plt.title('Survived rate of male')
plt.xlabel('Dead or Alive')
plt.ylabel('Number of People')
plt.xticks([0, 1], ['Death', 'Survived'])
plt.show()

plt.figure(figsize=(10, 6))
survival_counts_female.plot(kind='bar')
plt.title('Survived rate of female')
plt.xlabel('Dead or Alive')
plt.ylabel('Number of People')
plt.xticks([0, 1], ['Death', 'Survived'])
plt.show()
